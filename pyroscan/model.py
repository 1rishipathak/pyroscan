"""a small u-net for burn-scar change segmentation.

input: before + after sentinel-2 bands (5 + 5) and a validity channel = 11 channels at 20 m.
output: one logit per pixel, "this field burned between the two dates".
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

IN_CH = 11


class ConvBlock(nn.Module):
    def __init__(self, cin: int, cout: int, drop: float = 0.0):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
            nn.Dropout2d(drop) if drop else nn.Identity(),
            nn.Conv2d(cout, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.net(x)


class UNet(nn.Module):
    def __init__(self, in_ch: int = IN_CH, base: int = 32, depth: int = 4):
        super().__init__()
        chs = [base * 2**i for i in range(depth + 1)]  # 32 64 128 256 512
        self.downs = nn.ModuleList()
        c = in_ch
        for i, ch in enumerate(chs):
            self.downs.append(ConvBlock(c, ch, drop=0.1 if i >= depth - 1 else 0.0))
            c = ch
        self.ups = nn.ModuleList()
        self.up_convs = nn.ModuleList()
        for ch in reversed(chs[:-1]):
            self.ups.append(nn.ConvTranspose2d(c, ch, 2, stride=2))
            self.up_convs.append(ConvBlock(ch * 2, ch))
            c = ch
        self.head = nn.Conv2d(c, 1, 1)

    def forward(self, x):
        skips = []
        for i, block in enumerate(self.downs):
            x = block(x)
            if i < len(self.downs) - 1:
                skips.append(x)
                x = F.max_pool2d(x, 2)
        for up, conv, skip in zip(self.ups, self.up_convs, reversed(skips)):
            x = up(x)
            x = conv(torch.cat([x, skip], 1))
        return self.head(x)


def masked_loss(logits: torch.Tensor, target: torch.Tensor, ignore: int = 255) -> torch.Tensor:
    """bce + soft dice, both only on labelled pixels."""
    logits = logits.squeeze(1)
    m = target != ignore
    if m.sum() == 0:
        return logits.sum() * 0
    y = target.float().clamp(0, 1)
    bce = F.binary_cross_entropy_with_logits(logits[m], y[m])
    p = torch.sigmoid(logits)[m]
    inter = (p * y[m]).sum()
    dice = 1 - (2 * inter + 1) / (p.sum() + y[m].sum() + 1)
    return bce + 0.5 * dice
