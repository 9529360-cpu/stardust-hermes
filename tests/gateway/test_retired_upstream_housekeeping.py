"""The local gateway no longer schedules upstream skill replication."""

def test_housekeeping_has_no_upstream_sync_chores(monkeypatch):
    import gateway.run as gateway

    labels = []
    monkeypatch.setattr(gateway, "_housekeeping_chore", lambda label, fn: labels.append(label))

    class SixtyTicks:
        ticks = 0

        def is_set(self):
            return self.ticks >= 60

        def wait(self, timeout=None):
            self.ticks += 1

    gateway._start_gateway_housekeeping(SixtyTicks(), interval=0)
    assert "Curator tick" in labels
    assert not any("sync" in label.lower() for label in labels)
