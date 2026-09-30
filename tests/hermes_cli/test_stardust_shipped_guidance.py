"""Product-authority regression for shipped Stardust provider guidance."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SHIPPED_GUIDANCE = (
    ROOT / "cli-config.yaml.example",
    ROOT / "hermes_cli" / "tips.py",
    ROOT / "skills" / "autonomous-ai-agents" / "hermes-agent" / "references" / "providers-and-models.md",
)

FORBIDDEN = (
    "hermes auth add nous",
    "Nous-approved",
    "Nous Portal OAuth",
)


def test_shipped_guidance_does_not_recommend_nous_account_product() -> None:
    for path in SHIPPED_GUIDANCE:
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            assert phrase not in text, f"{path.relative_to(ROOT)} still contains retired guidance: {phrase}"
