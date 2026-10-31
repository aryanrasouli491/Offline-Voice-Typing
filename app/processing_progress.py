# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Measured estimates with fixed-cost fitting and persistent local calibration.

Model callbacks are evidence; elapsed time is only an estimate. The display is
monotonic and reserves 100% for the worker's actual completion notification.
"""
import json
import math
from pathlib import Path
from statistics import median
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

    @staticmethod
    def _weighted_median(values, weights):
        """Return a median that respects recency and duration similarity."""
        ordered = sorted(zip(values, weights), key=lambda item: item[0])
        halfway = sum(weights) / 2
        total = 0.0
        for value, weight in ordered:
            total += weight
            if total >= halfway:
                return value
        return ordered[-1][0]

    @staticmethod
    def _fit(rows, weights):
        """Fit fixed CPU overhead plus per-second audio cost."""
        total = sum(weights)
        mean_x = sum(w*x for w, (x, _) in zip(weights, rows)) / total
        mean_y = sum(w*y for w, (_, y) in zip(weights, rows)) / total
        variance = sum(w*(x-mean_x)**2 for w, (x, _) in zip(weights, rows)) / total
        if variance >= 4:
            slope = sum(w*(x-mean_x)*(y-mean_y)
                        for w, (x, y) in zip(weights, rows)) / (total*variance)
            slope = max(0.0, min(10.0, slope))
        else:
            # With similarly-sized recordings, most observed time is the fixed
            # encoder/decoder cost. Avoid inventing a steep duration slope.
            slope = min(.10, mean_y/max(1.0, mean_x)*.5)
        intercept = max(.15, mean_y-slope*mean_x)
        return intercept, slope

    def estimate(self, key, duration):
        """Fit elapsed = fixed startup cost + audio duration * processing rate.

        Short recordings spend much of their time in the fixed encoder cost;
        scaling their full runtime by audio length was wrong. Nearby, recent
        measurements get more weight when CPU load changes.
        """
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0.0
        duration = max(0.0, duration) if math.isfinite(duration) else 0.0
        rows = self.timings.get(self._key(key), [])
        if not rows:
            # A realistic first-run estimate for CPU Whisper-small. Local
            # measurements replace it immediately after the first conversion.
            return max(1.6, 1.6 + .10 * duration)
        weights = [.9 ** (len(rows)-1-i) / (1+abs(x-duration)/max(10, duration))
                   for i, (x, _) in enumerate(rows)]

        # Seed the robust pass with a Theil-Sen style median. Unlike ordinary
        # least squares, one very slow background-loaded run cannot pull this
        # initial line far away from the normal measurements.
        pair_slopes = [(y2-y1)/(x2-x1)
                       for i, (x1, y1) in enumerate(rows)
                       for x2, y2 in rows[i+1:] if abs(x2-x1) >= 1.0]
        if len(rows) >= 3 and pair_slopes:
            slope = max(0.0, min(10.0, median(pair_slopes)))
            intercept = max(.15, median([y-slope*x for x, y in rows]))
        else:
            intercept, slope = self._fit(rows, weights)

        # A busy CPU can produce one very slow run. Reweight large residuals so
        # a single outlier does not make every later progress bar inaccurate.
        residuals = [abs(y-(intercept+slope*x)) for x, y in rows]
        scale = max(.15, median(residuals)*1.4826)
        robust_weights = [w * min(1.0, 2.5*scale/max(scale, residual))
                          for w, residual in zip(weights, residuals)]
        intercept, slope = self._fit(rows, robust_weights)

        fitted = intercept + slope*duration
        local_predictions = [y+slope*(duration-x) for x, y in rows]
        nearby = self._weighted_median(local_predictions, robust_weights)
        return max(.35, .65*fitted + .35*nearby)

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
        candidate = self.estimate(self.key, self.duration)
        if self.started is not None:
            elapsed = max(0.0, self.clock()-self.started)
            # Never move the predicted finish behind the time already spent.
            # If the model supplied real segment progress, blend that evidence
            # into the new VAD-duration estimate instead of making a large jump.
            if self.floor >= .08:
                observed_total = elapsed/max(.08, self.floor)
                candidate = .7*candidate + .3*observed_total
            candidate = max(candidate, elapsed+.08)
        self.expected = candidate

    def report(self, fraction):
        fraction = min(.995, max(0.0, fraction))
        if self.started is not None and .08 <= fraction < .9 and fraction > self.floor:
            elapsed = max(0.0, self.clock()-self.started)
            observed_total = elapsed/fraction
            # Later model callbacks are stronger timing evidence. Bound extreme
            # callbacks, but adapt quickly when CPU load genuinely changes.
            observed_total = max(.35*self.expected, min(6*self.expected, observed_total))
            confidence = min(.75, .18+.62*fraction)
            self.expected = max(elapsed+.05,
                                (1-confidence)*self.expected + confidence*observed_total)
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
            # Reach 96.5% near the calibrated finish time, then approach 99.5%
            # gently. Only the worker's completion event is allowed to show 100%.
            estimate = (.965*ratio if ratio <= 1 else
                        .965+.03*(1-math.exp(-1.4*(ratio-1))))
            self.value = max(self.value, self.floor, estimate)
        return min(.995, self.value)


def overall_fraction(weights, finished_count, current):
    """Weight queued phrases by expected work, not by an equal job count."""
    if not weights:
        return 0.0
    index = min(finished_count, len(weights))
    done = sum(weights[:index])
    if index < len(weights):
        done += weights[index] * max(0, min(.995, current))
    return min(.995, done/max(.001, sum(weights)))
