use windows_ollama_agent_desktop_lib::discovery::{validate_action, validate_health};
use serde_json::json;
#[test]
fn execution_is_denied_for_every_desktop_rpc_alias() {
    for action in ["tools/call", "tool_call", "grant", "permit", "shell", "broker_dispatch"] {
        assert_eq!(validate_action(action).unwrap_err(), "formal_execution_disabled_review036");
    }
    assert!(validate_action("connect").is_ok());
}
#[test]
fn health_must_match_component_version_and_disabled_execution() {
    let health=json!({"component":"agent-discovery","version":env!("CARGO_PKG_VERSION"),"protocol":"1.0","status":"ok","execution_enabled":false});
    assert!(validate_health(&health).is_ok());
    for bad in [json!({}),json!({"component":"agent-discovery","version":"0.35.0","protocol":"1.0","status":"ok","execution_enabled":false}),json!({"component":"agent-discovery","version":env!("CARGO_PKG_VERSION"),"protocol":"1.0","status":"ok","execution_enabled":true})] {
        assert!(validate_health(&bad).is_err());
    }
}
