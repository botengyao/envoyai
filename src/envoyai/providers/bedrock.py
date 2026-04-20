"""AWS Bedrock providers — native Bedrock and Anthropic-on-Bedrock."""
from __future__ import annotations

from typing import ClassVar

from envoyai.auth.aws import AWSCredential
from envoyai.providers.base import Provider


class Bedrock(Provider):
    """AWS Bedrock (native API).

    Example::

        bedrock = envoyai.Bedrock(region="us-east-1", credentials=envoyai.aws.irsa())
        gw.model("titan").route(primary=bedrock("amazon.titan-text-premier-v1:0"))
    """

    region: str
    credentials: AWSCredential

    _schema: ClassVar[str] = "AWSBedrock"


class AWSAnthropic(Provider):
    """Anthropic Messages API served via AWS Bedrock.

    Accepts both OpenAI-format requests (translated) and native Anthropic
    Messages requests (passthrough). Use this when you want Claude models and
    your auth is AWS.
    """

    region: str
    credentials: AWSCredential

    _schema: ClassVar[str] = "AWSAnthropic"
