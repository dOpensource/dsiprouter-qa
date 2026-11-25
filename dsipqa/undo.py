"""Cleanup stack: every change a test makes registers how to undo it.

Undo actions run in reverse order after the test, pass or fail, and keep going when
one fails so a single error doesn't strand the rest. Failures are returned so the
test run can report them loudly: a dirty QA box breaks the next person's tests.
"""


class Undo:
    def __init__(self, log=print):
        self.stack = []
        self.log = log

    def push(self, name, fn):
        self.stack.append((name, fn))

    def run(self):
        failures = []
        while self.stack:
            name, fn = self.stack.pop()
            try:
                fn()
                self.log("  clean {}".format(name))
            except Exception as e:  # noqa: BLE001 - report every cleanup failure
                failures.append("{}: {}".format(name, e))
                self.log("  CLEANUP FAILED {} - {}".format(name, e))
        return failures
