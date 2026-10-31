# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Measured estimates with fixed-cost fitting and persistent local calibration.

Model callbacks are evidence; elapsed time is only an estimate. The display is
monotonic and reserves 100% for the worker's actual completion notification.
"""
import json
import math
from pathlib import Path
import time


class ProcessingProgress:
    def __init__(self, clock=time.monotonic, cache_path=None):
        self.clock = clock
        self.cache_path = Path(cache_path) if cache_path else None
        self.timings = {}
        self._load()
        self.reset()

    @staticmethod
    def _key(key):
        return json.dumps(key, ensure_ascii=False)

    def _load(self):
        if not self.cache_path:
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            for key, rows in list(data.items())[-32:]:
                if not isinstance(rows, list):
                    continue
                valid = []
                for row in rows[-20:]:
                    if (isinstance(row, list) and len(row) == 2
                            and all(type(v) in (int, float) and math.isfinite(v) and 0 < v < 86400 for v in row)):
                        valid.append(tuple(row))
                if valid:
                    self.timings[key] = valid
        except (OSError, ValueError):
            pass  # Calibration is optional; corrupt data must not block dictation.

    def save(self):
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.timings), encoding="utf-8")
        temporary.replace(self.cache_path)

    def reset(self):
        self.started = None
        self.key = None
        self.duration = 0.0
        self.expected = 3.0
        self.floor = self.value = 0.0
        self.completed = False

    def estimate(self, key, duration):
        """Fit elapsed = fixed startup cost + audio duration * processing rate.

        Short recordings spend much of their time in the fixed encoder cost;
        scaling their full runtime by audio length was wrong. Nearby, recent
        measurements get more weight when CPU load changes.
        """
        duration = max(0, duration)
        rows = self.timings.get(self._key(key), [])
        if not rows:
            return max(1.4, 1.4 + .1 * duration)
        weights = [.9 ** (len(rows)-1-i) / (1+abs(x-duration)/max(10, duration))
                   for i, (x, _) in enumerate(rows)]
        total = sum(weights)
        mean_x = sum(w*x for w, (x, _) in zip(weights, rows))/total
        mean_y = sum(w*y for w, (_, y) in zip(weights, rows))/total
        variance = sum(w*(x-mean_x)**2 for w, (x, _) in zip(weights, rows))/total
        if variance >= 4:
            slope = sum(w*(x-mean_x)*(y-mean_y) for w, (x, y) in zip(weights, rows)) / (total*variance)
            slope = max(0, min(10, slope))
        else:
            # Too little variation to fit both terms: keep a modest per-second
            # cost and calibrate the fixed term from actual measurements.
            slope = min(.1, mean_y/max(1, mean_x)*.5)
        intercept = max(.15, mean_y-slope*mean_x)
        return max(.3, intercept+slope*duration)

    def start(self, key, duration, started=None):
        self.key = key
        self.started = self.clock() if started is None else min(started, self.clock())
        self.duration = max(0, duration)
        self.expected = self.estimate(key, self.duration)
        self.floor = self.value = 0.0
        self.completed = False

    def set_duration(self, duration):
        # File duration and VAD speech duration become known after decoding.
        self.duration = max(0, duration)
        self.expected = self.estimate(self.key, self.duration)

    def report(self, fraction):
        fraction = min(.98, max(0.0, fraction))
        if self.started is not None and .08 <= fraction < .9 and fraction > self.floor:
            observed_total = (self.clock()-self.started)/fraction
            self.expected = .6*self.expected + .4*max(.3, observed_total)
        self.floor = max(self.floor, fraction)

    def learn(self, key, duration, seconds):
        if not all(math.isfinite(v) and 0 < v < 86400 for v in (duration, seconds)):
            return
        name = self._key(key)
        self.timings.setdefault(name, []).append((duration, seconds))
        self.timings[name] = self.timings[name][-20:]
        while len(self.timings) > 32:
            del self.timings[next(iter(self.timings))]

    def finish(self):
        self.completed = True
        self.value = 1.0

    def fraction(self):
        if self.completed:
            return 1.0
        if self.started is not None:
            elapsed = max(0, self.clock()-self.started)
            ratio = elapsed/max(.3, self.expected)
            # Reach 92% near the calibrated finish time, then move gently if the
            # CPU is slower this time. A timer cannot claim actual completion.
            estimate = .92*ratio if ratio <= 1 else .92+.06*(1-math.exp(-(ratio-1)))
            self.value = max(self.value, self.floor, estimate)
        return min(.98, self.value)


def overall_fraction(weights, finished_count, current):
    """Weight queued phrases by expected work, not by an equal job count."""
    if not weights:
        return 0.0
    index = min(finished_count, len(weights))
    done = sum(weights[:index])
    if index < len(weights):
        done += weights[index] * max(0, min(.98, current))
    return min(.98, done/max(.001, sum(weights)))
