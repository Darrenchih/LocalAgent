from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

AGENT_DIRECTORY = Path(r"D:\Agent")
TOPIC_FILE = AGENT_DIRECTORY / "ntfy-topic.txt"
DEFAULT_STATUS_MESSAGE = "Local Agent status: notification channel verified."


def read_topic() -> str:
    topic = TOPIC_FILE.read_text(encoding="utf-8").strip()

    if not topic or "\n" in topic or "\r" in topic:
        raise ValueError("ntfy-topic.txt must contain exactly one non-empty topic.")

    return topic


def send_status(message: str = DEFAULT_STATUS_MESSAGE) -> None:
    if not message or "\n" in message or "\r" in message or len(message) > 200:
        raise ValueError("Status message must be one non-empty line of 200 characters or fewer.")

    endpoint = f"https://ntfy.sh/{quote(read_topic(), safe='')}"
    request = Request(
        endpoint,
        data=message.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )

    with urlopen(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}.")


if __name__ == "__main__":
    try:
        send_status()
        print("Status notification sent.")
    except (OSError, ValueError, HTTPError, URLError, RuntimeError) as error:
        print(f"Status notification failed: {error}")
        raise SystemExit(1)
