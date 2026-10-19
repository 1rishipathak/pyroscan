"""u-net shapes (skipped when pytorch is not installed, e.g. with requirements-app.txt)."""
import pytest

torch = pytest.importorskip("torch")


def test_unet_keeps_the_image_size():
    from pyroscan.model import IN_CH, UNet, masked_loss

    m = UNet().eval()
    x = torch.randn(2, IN_CH, 64, 64)
    y = m(x)
    assert y.shape == (2, 1, 64, 64)
    target = torch.full((2, 64, 64), 255)
    target[:, :8, :8] = 1
    target[:, 8:16, :8] = 0
    assert torch.isfinite(masked_loss(y, target))
