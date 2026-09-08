import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ADMIN_EMAIL']='admin@example.com'
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
os.environ['JWT_SECRET']='test-only-secret'
os.environ['PLUNK_SECRET_KEY']='test-key'
os.environ['PLUNK_FROM_ADDRESS']='sender@example.com'
os.environ['NEWSLETTER_ENABLED']='true'
os.environ['CAMPAIGN_WORKER_ENABLED']='true'
