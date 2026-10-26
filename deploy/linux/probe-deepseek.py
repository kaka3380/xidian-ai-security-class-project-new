"""Read a user-specified key in memory; never print credentials or raw error bodies."""
import argparse
import json
from pathlib import Path
import re
import urllib.request
import urllib.error


def load_key(path):
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    keys = set(re.findall(r"sk-[A-Za-z0-9_-]{16,}", text))
    if len(keys) != 1:
        raise ValueError("credential file must contain exactly one recognizable API key")
    return keys.pop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("key_file")
    args = parser.parse_args()
    key = load_key(args.key_file)
    evidence = {"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "budget_cny": 5, "model_requests": 0}
    for endpoint in ("models", "user/balance"):
        request = urllib.request.Request("https://api.deepseek.com/" + endpoint, headers={"Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            if endpoint == "models":
                evidence["models"] = [m["id"] for m in data["data"]]
            else:
                evidence["balance_available"] = data.get("is_available")
                evidence["balance_currencies"] = [b.get("currency") for b in data.get("balance_infos", [])]
        except urllib.error.HTTPError as error:
            evidence[endpoint + "_error"] = {"http_status": error.code}
        except (urllib.error.URLError, TimeoutError):
            evidence[endpoint + "_error"] = {"kind": "connection_failed"}
    target = Path(__file__).parent / "evidence/deepseek-preflight.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
