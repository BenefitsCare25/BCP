"""Durable AI claim-review queue, stages, recovery, and observability.

Production execution enters through ``app.workers.claim_review``. The package
stays import-light so loading one stage does not pull in the entire AI gateway.
"""
