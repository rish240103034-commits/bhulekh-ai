"""Shared test configuration.

Runs before any test module imports the app, so environment-driven settings (rate
limiting, the signing key, the database URL) are in place at import time.
"""
import os

os.environ.setdefault("BHULEKH_ENVIRONMENT", "development")
os.environ.setdefault("BHULEKH_SECRET_KEY", "test-secret-key-not-for-production-use-only-x")
os.environ.setdefault("BHULEKH_RATE_LIMIT_ENABLED", "false")   # tests hammer login/upload
os.environ.setdefault("BHULEKH_LOG_JSON", "false")
os.environ.setdefault("BHULEKH_DATABASE_URL", "sqlite:///./test_bhulekh.db")
os.environ.setdefault("BHULEKH_STORAGE_DIR", "./test_storage")
os.environ.setdefault("BHULEKH_UPLOAD_DIR", "./test_storage/uploads")
os.environ.setdefault("BHULEKH_PROCESSED_DIR", "./test_storage/processed")
