import os

# Never call the real API from tests.
os.environ.setdefault("NEUROINFOAPI_DISABLE_BOOT_CHECK", "1")
