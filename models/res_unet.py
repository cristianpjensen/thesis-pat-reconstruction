import torch
import torch.nn as nn
from .gan import GAN, Discriminator


class ResUnetGAN(GAN):
    """Implementation of a GAN with a residual U-net.

    :param in_channels: Input channels that can vary if the images are
        grayscale or color.
    :param out_channels: Input channels that can vary if the images are
        grayscale or color.
    :param channel_mults: Channel multiples that define the depth and width of
        the U-net architecture.
    :param dropout: Dropout percentage used in some of the decoder blocks.
    :param l1_lambda: How much the L1 loss should be weighted in the loss
        function.

    :input: [N x in_channels x H x W]
    :output: [N x out_channels x H x W]

    """

    def __init__(
        self,
        in_channels: int = 3,
        out_channels: int = 3,
        channel_mults: tuple[int] = (1, 2, 4, 8, 8, 8, 8, 8),
        dropout: float = 0.5,
        l1_lambda: float = 50,
    ):
        generator = ResUnet(
            in_channels,
            out_channels,
            channel_mults=channel_mults,
            dropout=dropout,
        )

        discriminator = Discriminator(in_channels)

        super().__init__(generator, discriminator, l1_lambda=l1_lambda)

        self.example_input_array = torch.Tensor(2, in_channels, 256, 256)
        self.save_hyperparameters()


class EncoderBlock(nn.Module):
    """Encoder block that downsamples the input by 2.

    :param in_channels: Input channels.
    :param out_channels: Output channels.
    :param norm: Whether to use batch normalization or not.

    :input: [N x in_channels x H x W]
    :output: [N x out_channels x (H / 2) x (W / 2)]

    """

    def __init__(self, in_channels: int, out_channels: int, norm: bool = True):
        super().__init__()

        self.conv_block = nn.Sequential(
            nn.LeakyReLU(0.2),
            nn.Conv2d(
                in_channels,
                out_channels,
                kernel_size=4,
                stride=2,
                padding=1
            ),
            nn.BatchNorm2d(out_channels),

            nn.LeakyReLU(0.2),
            nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channels) if norm else nn.Identity(),
        )

        self.conv_skip = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size=1,
            stride=2,
        )

    def forward(self, x):
        return self.conv_block(x) + self.conv_skip(x)


class DecoderBlock(nn.Module):
    """Decoder block that upsamples the input by 2.

    :param in_channels: Input channels.
    :param out_channels: Output channels.
    :param dropout: Dropout percentage.

    :input: [N x in_channels x H x W]
    :output: [N x out_channels x (H * 2) x (W * 2)]

    """

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        dropout: float = 0.5,
    ):
        super().__init__()

        self.conv_block = nn.Sequential(
            nn.ReLU(),
            nn.ConvTranspose2d(
                in_channels,
                out_channels,
                kernel_size=4,
                stride=2,
                padding=1
            ),
            nn.BatchNorm2d(out_channels),

            nn.ReLU(),
            nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channels),

            nn.Dropout2d(dropout) if dropout > 0 else nn.Identity(),
        )

        self.conv_skip = nn.ConvTranspose2d(
            in_channels,
            out_channels,
            kernel_size=2,
            stride=2,
        )

    def forward(self, x):
        return self.conv_block(x) + self.conv_skip(x)


class ResUnet(nn.Module):
    """U-net used as the generator in pix2pix GAN.

    :param in_channels: Input channels that can vary if the images are
        grayscale or color.
    :param out_channels: Input channels that can vary if the images are
        grayscale or color.
    :param channel_mults: Channel multiples that define the depth and width of
        the U-net architecture.
    :param dropout: Dropout percentage used in some of the decoder blocks.

    :input: [N x in_channels x H x W]
    :output: [N x out_channels x H x W]

    """

    def __init__(
        self,
        in_channels: int = 3,
        out_channels: int = 3,
        channel_mults: tuple[int] = (1, 2, 4, 8, 8, 8, 8, 8),
        dropout: float = 0.5,
    ):
        super().__init__()

        # Encoder blocks
        encoders = [
            nn.Conv2d(
                in_channels,
                channel_mults[0] * 64,
                kernel_size=4,
                stride=2,
                padding=1
            ),
        ]
        in_channels = channel_mults[0] * 64
        for level, mult in enumerate(channel_mults[1:], 1):
            channels = mult * 64

            encoders.append(
                EncoderBlock(
                    in_channels,
                    channels,
                    norm=level != len(channel_mults) - 1,
                )
            )

            in_channels = channels

        self.encoders = nn.ModuleList(encoders)

        # Decoder blocks
        decoders = []
        for level, mult in reversed(list(enumerate(channel_mults[:-1]))):
            channels = mult * 64

            decoders.append(
                DecoderBlock(
                    in_channels,
                    channels,
                    # Only dropout in the lowest three decoder blocks that are
                    # at the widest part
                    dropout=dropout if (
                        mult == max(channel_mults) and
                        level > len(channel_mults) - 5
                    ) else 0,
                )
            )

            in_channels = channels * 2

        decoders.append(
            nn.ConvTranspose2d(
                in_channels,
                out_channels,
                kernel_size=4,
                stride=2,
                padding=1,
            )
        )

        self.decoders = nn.ModuleList(decoders)
        self.out = nn.Tanh()

    def forward(self, x):
        h = x.type(torch.float32)

        feats = []
        for encoder in self.encoders:
            h = encoder(h)
            feats.append(h)

        # Remove last feature map, since that should not be used in
        # skip-connection
        feats.pop()

        for index, decoder in enumerate(self.decoders):
            if index != 0:
                h = torch.cat([h, feats.pop()], dim=1)

            h = decoder(h)

        return self.out(h)