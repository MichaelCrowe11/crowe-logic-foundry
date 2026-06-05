from control_plane import agents


def test_human_token_is_not_agent():
    claims = {
        "sub": "kc-sub-1",
        "preferred_username": "michael@crowelogic.com",
        "email": "michael@crowelogic.com",
        "crowe_tier": "enterprise",
    }
    assert agents.is_agent_token(claims) is False


def test_service_account_token_is_agent():
    claims = {
        "sub": "svc-sub-9",
        "preferred_username": "service-account-agent-alpha",
        "clientId": "agent-alpha",
        "azp": "agent-alpha",
        "crowe_tier": "pro",
    }
    assert agents.is_agent_token(claims) is True


def test_agent_principal_shape():
    claims = {
        "sub": "svc-sub-9",
        "preferred_username": "service-account-agent-alpha",
        "clientId": "agent-alpha",
        "azp": "agent-alpha",
        "crowe_tier": "pro",
    }
    p = agents.agent_principal(claims)
    assert p == {
        "principal": "crowe-agent",
        "client_id": "agent-alpha",
        "workspace_id": "agent-alpha",
        "user_id": "svc-sub-9",
        "plan_id": "pro",
        "subject": "service-account-agent-alpha",
    }


def test_agent_principal_falls_back_to_azp_then_sub():
    claims = {
        "sub": "svc-sub-7",
        "preferred_username": "service-account-x",
        "azp": "x",
        "crowe_tier": "free",
    }
    assert agents.agent_principal(claims)["client_id"] == "x"
    assert (
        agents.agent_principal(
            {"sub": "only-sub", "preferred_username": "service-account-only"}
        )["client_id"]
        == "only-sub"
    )
