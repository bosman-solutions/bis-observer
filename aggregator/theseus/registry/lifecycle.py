"""
Entity lifecycle: what an entity IS (class) and where it is (state).

class, first match wins:
  operator override (entity.lifecycle)   persistent | ephemeral
  kind == node                           persistent
  compose-managed container              persistent   (compose labels from docker-inventory)
  anything else                          ephemeral    (drone steps, scanner jobs, docker run one-offs)

state:
  up        seen within OFFLINE_AFTER
  offline   persistent, unseen — stays on the board, red, until retired
  ended     ephemeral, unseen — never red; pruned after EPHEMERAL_TTL
"""
import os

OFFLINE_AFTER = int(os.getenv("REGISTRY_OFFLINE_AFTER", "600"))    # 10 min
EPHEMERAL_TTL = int(os.getenv("REGISTRY_EPHEMERAL_TTL", "3600"))   # 1 h


def classify(e):
    if e.get("lifecycle") in ("persistent", "ephemeral"):
        return e["lifecycle"]
    if e["kind"] == "node":
        return "persistent"
    if e["labels"].get("compose_service") or e["labels"].get("compose_project"):
        return "persistent"
    return "ephemeral"


def state(e, now):
    if now - e["last_seen"] <= OFFLINE_AFTER:
        return "up"
    return "offline" if classify(e) == "persistent" else "ended"


def annotate(e, now):
    e["class"] = classify(e)
    e["state"] = state(e, now)
    return e


def prunable(e, now):
    return classify(e) == "ephemeral" and now - e["last_seen"] > EPHEMERAL_TTL
