"""AI claim-review pipeline package.

Entry point: ``pipeline.run_review`` (invoked via FastAPI BackgroundTasks on
claim submit and the broker rerun endpoint). Kept import-light — import the
submodules directly (``from app.services.claims_review.pipeline import
run_review``) so pulling one stage in doesn't drag the whole pipeline (and its
AI gateway import chain) along.
"""
