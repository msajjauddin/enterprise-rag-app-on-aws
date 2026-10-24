import streamlit as st
import requests
import json
import base64
import hashlib
import os
import secrets
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file if it exists
load_dotenv()

# Configuration from environment variables (see .env.example; CI publishes these as an artifact)
API_BASE = (os.getenv("API_ENDPOINT") or "").rstrip("/")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8501/")
COGNITO_BASE = f"https://{COGNITO_DOMAIN}.auth.{AWS_REGION}.amazoncognito.com"

# Set page config
st.set_page_config(
    page_title="RAG Application",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Session state initialization
DEFAULTS = {
    "authenticated": False,
    "access_token": None,
    "id_token": None,
    "refresh_token": None,
    "token_expiry": None,
    "user_email": None,
    "user_groups": [],
    "messages": [],          # current conversation: {"role", "content", "sources", "message_id", ...}
    "conversation_id": None,
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# =========================
# Authentication (Cognito hosted UI, authorization code + PKCE)
# =========================

@st.cache_resource
def pending_logins():
    """PKCE verifiers by OAuth state. Server-side, because the redirect starts a new Streamlit session."""
    return {}


def build_login_url():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    pending = pending_logins()
    # Drop stale attempts
    for old_state, (_, created) in list(pending.items()):
        if time.time() - created > 600:
            pending.pop(old_state, None)
    pending[state] = (verifier, time.time())

    params = {
        "response_type": "code",
        "client_id": COGNITO_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{COGNITO_BASE}/oauth2/authorize?{urlencode(params)}"


def decode_jwt_claims(token):
    """Read claims for display only. The API validates the token signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def store_tokens(tokens):
    st.session_state.access_token = tokens["access_token"]
    st.session_state.id_token = tokens.get("id_token", st.session_state.id_token)
    if tokens.get("refresh_token"):
        st.session_state.refresh_token = tokens["refresh_token"]
    st.session_state.token_expiry = datetime.now() + timedelta(seconds=tokens.get("expires_in", 3600))

    claims = decode_jwt_claims(st.session_state.id_token or "")
    st.session_state.user_email = claims.get("email")
    st.session_state.user_groups = sorted(claims.get("cognito:groups", []))
    st.session_state.authenticated = True


def exchange_code_for_tokens(code, state):
    entry = pending_logins().pop(state, None)
    if entry is None:
        return False, "Sign-in session expired. Please sign in again."
    verifier, _ = entry
    response = requests.post(
        f"{COGNITO_BASE}/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": COGNITO_CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if response.status_code != 200:
        logger.error(f"Token exchange failed: {response.status_code} {response.text}")
        return False, "Sign-in failed. Please try again."
    store_tokens(response.json())
    return True, "Signed in"


def handle_oauth_callback():
    params = st.query_params
    if "code" in params and "state" in params:
        success, message = exchange_code_for_tokens(params["code"], params["state"])
        st.query_params.clear()
        if not success:
            st.error(message)
        else:
            st.rerun()
    elif "error" in params:
        st.error(f"Sign-in failed: {params.get('error_description', params['error'])}")
        st.query_params.clear()


# Check if token needs to be refreshed
def check_token_refresh():
    if not st.session_state.authenticated:
        return False
    if st.session_state.token_expiry and st.session_state.token_expiry < datetime.now() + timedelta(minutes=5):
        if not st.session_state.refresh_token:
            logout_user()
            return False
        response = requests.post(
            f"{COGNITO_BASE}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": COGNITO_CLIENT_ID,
                "refresh_token": st.session_state.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if response.status_code != 200:
            logger.warning("Token refresh failed, logging out user")
            logout_user()
            return False
        store_tokens(response.json())
        logger.info("Token refreshed successfully")
    return True


# Function to log out the user
def logout_user():
    for key, value in DEFAULTS.items():
        st.session_state[key] = value if not isinstance(value, list) else []


def logout_url():
    return f"{COGNITO_BASE}/logout?{urlencode({'client_id': COGNITO_CLIENT_ID, 'logout_uri': REDIRECT_URI})}"


def get_headers():
    return {"Authorization": f"Bearer {st.session_state.access_token}"}


def api_request(method, path, **kwargs):
    """Call the query API with the user's access token. Returns the response or None after showing an error."""
    if not check_token_refresh():
        st.warning("Your session has expired. Please sign in again.")
        st.rerun()
    response = requests.request(method, f"{API_BASE}{path}", headers=get_headers(), timeout=kwargs.pop("timeout", 30), **kwargs)
    if response.status_code == 401:
        st.warning("Your session has expired. Please sign in again.")
        logout_user()
        st.rerun()
    return response


def error_message(response):
    try:
        return response.json().get("detail") or response.json().get("message") or response.text
    except Exception:
        return response.text


# Application title and description
st.title("RAG App on AWS")


# Function to render the login page
def render_login_page():
    st.subheader("Sign in")
    st.write("Use your company account to sign in.")
    if not (API_BASE and COGNITO_CLIENT_ID and COGNITO_DOMAIN):
        st.error("API_ENDPOINT, COGNITO_CLIENT_ID and COGNITO_DOMAIN must be set (see rag_ui/.env.example).")
        return
    st.markdown(
        f'<a href="{build_login_url()}" target="_self" '
        f'style="display:inline-block;padding:0.5rem 1.25rem;border-radius:0.5rem;'
        f'background:#2563eb;color:white;text-decoration:none;">Sign in with SSO</a>',
        unsafe_allow_html=True,
    )


# Add a user profile in the sidebar
def render_user_sidebar():
    st.sidebar.markdown("---")
    st.sidebar.subheader("User Profile")
    st.sidebar.write(f"Email: {st.session_state.user_email}")
    if st.session_state.user_groups:
        st.sidebar.write("Groups: " + ", ".join(st.session_state.user_groups))

    if st.session_state.token_expiry:
        minutes_left = int((st.session_state.token_expiry - datetime.now()).total_seconds() / 60)
        if minutes_left >= 0:
            st.sidebar.caption(f"Session refreshes in {minutes_left}m")

    if st.sidebar.button("Logout", use_container_width=True):
        url = logout_url()
        logout_user()
        st.sidebar.markdown(f'<meta http-equiv="refresh" content="0; url={url}">', unsafe_allow_html=True)


# =========================
# Documents
# =========================

def upload_document(file, share_with_groups):
    """Get a presigned POST from the API and upload the file straight to S3."""
    response = api_request("POST", "/documents/upload-url", json={
        "file_name": file.name,
        "content_type": file.type or "application/octet-stream",
        "share_with_groups": share_with_groups,
    })
    if response.status_code != 200:
        return False, f"Upload failed: {error_message(response)}"

    result = response.json()
    upload = result["upload"]
    if file.size > result["max_bytes"]:
        return False, f"File is larger than {result['max_bytes'] // (1024 * 1024)} MB"

    s3_response = requests.post(
        upload["url"],
        data=upload["fields"],
        files={"file": (file.name, file.getvalue(), file.type or "application/octet-stream")},
        timeout=300,
    )
    if s3_response.status_code not in (200, 201, 204):
        logger.error(f"S3 upload failed: {s3_response.status_code} {s3_response.text}")
        return False, f"Upload to storage failed ({s3_response.status_code})"
    return True, result


def show_my_documents():
    response = api_request("GET", "/documents")
    if response.status_code != 200:
        st.error(f"Could not load documents: {error_message(response)}")
        return
    items = response.json()["items"]
    if not items:
        st.info("You haven't uploaded any documents yet.")
        return
    df = pd.DataFrame(items)
    if "updated_at" in df:
        df["updated_at"] = pd.to_datetime(df["updated_at"], unit="ms").dt.strftime("%Y-%m-%d %H:%M")
    if "shared_with" in df:
        df["shared_with"] = df["shared_with"].apply(lambda groups: ", ".join(groups) if isinstance(groups, list) else "")
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_upload_page():
    st.header("Upload Documents")
    st.write("Documents are private to you unless you share them with one of your groups. "
             "They become searchable a minute or two after upload.")

    uploaded_file = st.file_uploader("Choose a file to upload", type=["pdf", "txt", "md", "csv"])
    share_with = st.multiselect(
        "Share with groups",
        options=st.session_state.user_groups,
        help="Members of these groups will be able to get answers from this document.",
    )

    if uploaded_file is not None and st.button("Upload Document"):
        with st.spinner("Uploading document..."):
            success, result = upload_document(uploaded_file, share_with)
        if success:
            st.success(f"Uploaded {uploaded_file.name}. Document ID: {result['document_id']}")
        else:
            st.error(result)

    st.subheader("My documents")
    if st.button("Refresh"):
        st.rerun()
    show_my_documents()


# =========================
# Chat
# =========================

def stream_query(question, web_search):
    """POST /query and yield (event, data) pairs from the Server-Sent Events stream."""
    if not check_token_refresh():
        st.warning("Your session has expired. Please sign in again.")
        st.rerun()
    with requests.post(
        f"{API_BASE}/query",
        json={"question": question, "conversation_id": st.session_state.conversation_id, "web_search": web_search},
        headers={**get_headers(), "Accept": "text/event-stream"},
        stream=True,
        timeout=(10, 120),
    ) as response:
        if response.status_code != 200:
            yield "http_error", {"status": response.status_code, "message": error_message(response)}
            return
        event = None
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                yield event, json.loads(line[6:])
                event = None


def render_sources(sources, web_search_used):
    if not sources and not web_search_used:
        return
    with st.expander(f"Sources ({len(sources)} documents{' + web search' if web_search_used else ''})"):
        for source in sources:
            page = f", page {source['page'] + 1}" if isinstance(source.get("page"), int) else ""
            st.markdown(f"**{source['file_name']}**{page} · relevance {source['score']:.2f}")
            st.caption(source["snippet"])
        if web_search_used:
            st.info("Part of this answer comes from a web search because the documents didn't cover it well.")


def send_feedback(message_id, key):
    rating = st.session_state.get(key)
    if rating is None:
        return
    response = api_request("POST", "/feedback", json={"message_id": message_id, "rating": 1 if rating == 1 else -1})
    if response.status_code == 200:
        st.toast("Thanks for the feedback")


def render_assistant_extras(message):
    render_sources(message.get("sources", []), message.get("web_search_used", False))
    details = []
    if message.get("cached"):
        details.append("cached answer")
    if message.get("latency_ms"):
        details.append(f"{message['latency_ms'] / 1000:.1f}s")
    if details:
        st.caption(" · ".join(details))
    if message.get("message_id"):
        key = f"feedback_{message['message_id']}"
        st.feedback("thumbs", key=key, on_change=send_feedback, args=(message["message_id"], key))


def render_chat_page():
    st.header("Ask your documents")

    col1, col2 = st.columns([3, 1])
    with col2:
        web_search = st.toggle("Allow web search", value=True,
                               help="Search the web when your documents don't answer the question well.")
        if st.button("New conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.conversation_id = None
            st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_assistant_extras(message)

    question = st.chat_input("e.g., How many days of annual leave do I get?")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("_Searching your documents…_")
        answer, reply = "", {"role": "assistant", "sources": []}

        for event, data in stream_query(question, web_search):
            if event == "meta":
                reply.update({k: data.get(k) for k in ("message_id", "sources", "cached", "web_search_used")})
                st.session_state.conversation_id = data["conversation_id"]
            elif event == "token":
                answer += data["text"]
                placeholder.markdown(answer + "▌")
            elif event == "replace":
                answer = data["text"]
            elif event == "done":
                reply["latency_ms"] = data.get("latency_ms")
            elif event == "error":
                answer = data["message"]
            elif event == "http_error":
                if data["status"] == 401:
                    logout_user()
                    st.rerun()
                answer = f"Request failed ({data['status']}): {data['message']}"

        placeholder.markdown(answer)
        reply["content"] = answer
        render_assistant_extras(reply)
    st.session_state.messages.append(reply)


# =========================
# History
# =========================

# Function to create evaluation chart
def create_evaluation_chart(eval_results):
    """Create a visualization for the sampled online-evaluation scores"""
    metric_names = {"answer_relevancy": "Answer Relevancy", "faithfulness": "Faithfulness"}
    colors = {"answer_relevancy": "#2563eb", "faithfulness": "#16a34a"}
    metrics = {k: v for k, v in eval_results.items() if k in metric_names}

    fig = go.Figure(data=[
        go.Bar(
            x=[metric_names[k] for k in metrics],
            y=list(metrics.values()),
            text=[f"{v:.2f}" for v in metrics.values()],
            textposition="auto",
            marker_color=[colors[k] for k in metrics],
        )
    ])
    fig.update_layout(title="Evaluation", yaxis_title="Score (0-1)", yaxis_range=[0, 1],
                      template="plotly_white", height=300)
    return fig


def render_history_page():
    st.header("History")
    response = api_request("GET", "/history", params={"limit": 50})
    if response.status_code != 200:
        st.error(f"Could not load history: {error_message(response)}")
        return
    items = response.json()["items"]
    if not items:
        st.info("No questions yet.")
        return

    for item in items:
        asked = datetime.fromtimestamp(item["created_at"]).strftime("%Y-%m-%d %H:%M")
        with st.expander(f"{asked} · {item['question'][:100]}"):
            st.markdown(item["answer"])
            render_sources(item.get("sources", []), item.get("web_search_used", False))
            if item.get("feedback") is not None:
                st.caption("Your feedback: " + ("👍" if item["feedback"] > 0 else "👎"))
            if item.get("evaluation"):
                st.plotly_chart(create_evaluation_chart(item["evaluation"]), use_container_width=True,
                                key=f"eval_{item['message_id']}")
                st.caption(item["evaluation"].get("reason", ""))


# Function to render the sidebar with navigation and settings
def render_sidebar():
    st.sidebar.title("📚 App Navigation")
    if st.session_state.authenticated:
        page = st.sidebar.radio("Select an action:", ["Ask", "Upload Documents", "History"])
        render_user_sidebar()
    else:
        page = "Login"

    st.sidebar.markdown("---")
    st.sidebar.caption("🔖 Version: RAG App on AWS v2.0")
    return page


# Main function to run the Streamlit app
def main():
    handle_oauth_callback()
    page = render_sidebar()

    if page == "Login":
        render_login_page()
    elif page == "Upload Documents":
        render_upload_page()
    elif page == "History":
        render_history_page()
    else:
        render_chat_page()


# Run the app
if __name__ == "__main__":
    main()
