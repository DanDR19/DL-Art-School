import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from torch import einsum

from models.lucidrains.dalle.transformer import Transformer
from trainer.networks import register_model
from utils.util import opt_get


def exists(val):
    return val is not None


def masked_mean(t, mask, dim = 1):
    t = t.masked_fill(~mask[:, :, None], 0.)
    return t.sum(dim = 1) / mask.sum(dim = 1)[..., None]


class VoiceCLIP(nn.Module):
    """
    CLIP model retrofitted for performing contrastive evaluation between tokenized audio data and the corresponding
    transcribed text.

    Originally from https://github.com/lucidrains/DALLE-pytorch/blob/main/dalle_pytorch/dalle_pytorch.py
    """

    def __init__(
            self,
            *,
            dim_text=512,
            dim_speech=512,
            dim_latent=512,
            num_text_tokens=10000,
            text_enc_depth=6,
            text_seq_len=200,
            text_heads=8,
            num_speech_tokens=8192,
            speech_enc_depth=6,
            speech_heads=8,
            speech_seq_len=250,
    ):
        super().__init__()
        self.text_emb = nn.Embedding(num_text_tokens, dim_text)
        self.text_pos_emb = nn.Embedding(text_seq_len, dim_text)
        self.text_transformer = Transformer(causal=False, seq_len=text_seq_len, dim=dim_text, depth=text_enc_depth,
                                            heads=text_heads, rotary_emb=False)
        self.to_text_latent = nn.Linear(dim_text, dim_latent, bias=False)

        self.speech_emb = nn.Embedding(num_speech_tokens, dim_speech)
        self.speech_pos_emb = nn.Embedding(num_speech_tokens, dim_speech)
        self.speech_transformer = Transformer(causal=False, seq_len=speech_seq_len, dim=dim_speech,
                                              depth=speech_enc_depth, heads=speech_heads, rotary_emb=False)
        self.to_speech_latent = nn.Linear(dim_speech, dim_latent, bias=False)

        self.temperature = nn.Parameter(torch.tensor(1.))

    def forward(
            self,
            text,
            speech_tokens,
            text_mask=None,
            return_loss=False
    ):
        b, device = text.shape[0], text.device

        text_emb = self.text_emb(text)
        text_emb += self.text_pos_emb(torch.arange(text.shape[1], device=device))

        speech_emb = self.speech_emb(speech_tokens)
        speech_emb += self.speech_pos_emb(torch.arange(speech_emb.shape[1], device=device))

        enc_text = self.text_transformer(text_emb, mask=text_mask)
        enc_speech = self.speech_transformer(speech_emb)

        if exists(text_mask):
            text_latents = masked_mean(enc_text, text_mask, dim=1)
        else:
            text_latents = enc_text.mean(dim=1)

        speech_latents = enc_speech.mean(dim=1)

        text_latents = self.to_text_latent(text_latents)
        speech_latents = self.to_speech_latent(speech_latents)

        text_latents, speech_latents = map(lambda t: F.normalize(t, p=2, dim=-1), (text_latents, speech_latents))

        temp = self.temperature.exp()

        if not return_loss:
            sim = einsum('n d, n d -> n', text_latents, speech_latents) * temp
            return sim

        sim = einsum('i d, j d -> i j', text_latents, speech_latents) * temp
        labels = torch.arange(b, device=device)
        loss = (F.cross_entropy(sim, labels) + F.cross_entropy(sim.t(), labels)) / 2
        return loss


@register_model
def register_voice_clip(opt_net, opt):
    return VoiceCLIP(**opt_get(opt_net, ['kwargs'], {}))


if __name__ == '__main__':
    clip = VoiceCLIP()
    clip(torch.randint(0,1000,(2,200)),
         torch.randint(0,8192,(2,250)),
         return_loss=True)