"""Legacy Stripe SDK boundary; disabled for the selected Brainbase + Link route."""
import os
import re

import stripe


class StripeConfigurationError(ValueError):
    pass


def resource_dict(resource):
    """Stripe 15 resources are not dicts; normalize them at the SDK boundary."""
    return resource if isinstance(resource, dict) else resource.to_dict()


def test_client():
    if os.getenv("PAYMENT_PROVIDER", "brainbase_link") != "stripe_test":
        raise StripeConfigurationError(
            "Brainbase + Link is the selected payment approach. Its backend connector is not implemented; "
            "no payment was attempted. Legacy Stripe calls are disabled."
        )
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not re.fullmatch(r"sk_test_[A-Za-z0-9]{12,}", key):
        raise StripeConfigurationError(
            "Set a valid sk_test_ Stripe sandbox key in backend/.env; live keys and placeholders are refused"
        )
    return stripe.StripeClient(
        key, max_network_retries=2,
        http_client=stripe.RequestsClient(timeout=20),
    )
