"""Streamlit portals for authenticated, layout-preserving document sharing."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv
from agents.deid_agent import DeidAgent
from engine.service import DeidService

load_dotenv()
st.set_page_config(page_title="Health-DeID", page_icon="🏥", layout="wide")
DATA_DIR = os.getenv("DEID_DATA_DIR", str(Path(__file__).parent / ".deid-data"))


@st.cache_resource
def load_service(data_dir):
    return DeidService(data_dir)


def attachment(data, extension, reference):
    mime = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".txt": "text/plain"}[extension]
    if extension in {".png", ".jpg", ".jpeg"}:
        st.image(data, use_container_width=True)
    st.download_button("Download report", data, file_name=f"report_{reference}{extension}", mime=mime, key=f"download_{reference}")


def main():
    service = load_service(DATA_DIR)
    token = st.session_state.get("session")
    try:
        user = service.auth.require(token)
    except PermissionError:
        st.title("Health-DeID sign in")
        st.caption("Sign in with your assigned admin or doctor account.")
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in")
        if submitted:
            try:
                session = service.auth.login(username, password)
                st.session_state.clear()
                st.session_state["session"] = session
                st.rerun()
            except PermissionError as exc:
                st.error(str(exc))
        return

    st.sidebar.write(f"Signed in: {user['name']} ({user['role']})")
    if st.sidebar.button("Sign out"):
        service.auth.logout(token)
        st.session_state.clear()
        st.rerun()
    st.title("Admin report portal" if user["role"] == "admin" else "Doctor review portal")

    if user["role"] == "admin":
        upload_tab, returned_tab = st.tabs(["Create clean copy", "Returned reports"])
        with upload_tab:
            uploaded = st.file_uploader("Upload a medical report", type=["pdf", "png", "jpg", "jpeg", "txt"])
            st.caption("Analysis sends the document to the configured Google AI provider. Identifying fields and labels are removed from the clean copy. Review every page before sharing.")
            if uploaded is not None:
                raw = uploaded.getvalue()
                fingerprint = hashlib.sha256(raw + uploaded.name.encode()).hexdigest()
                if st.session_state.get("upload_fingerprint") != fingerprint:
                    for key in ["analysis", "preview", "preview_fingerprint", "approval"]:
                        st.session_state.pop(key, None)
                    st.session_state["upload_fingerprint"] = fingerprint
                if st.button("Scan report and identify PHI"):
                    with st.spinner("Analyzing every page…"):
                        try:
                            st.session_state["analysis"] = service.analyze(token, raw, uploaded.name, DeidAgent())
                            st.session_state.pop("preview", None)
                        except Exception as exc:
                            st.session_state.pop("analysis", None)
                            st.error(str(exc))
                if "analysis" in st.session_state:
                    analysis = copy.deepcopy(st.session_state["analysis"])
                    st.subheader("Review fields to remove")
                    st.caption("Selections should cover each identifying label and its full value. Coordinates run from 0 to 1000: top, left, bottom, right. Add missed identifiers before previewing.")
                    for number, entities in enumerate(analysis.detections):
                        with st.expander(f"Page {number + 1}", expanded=True):
                            if analysis.pages:
                                st.image(analysis.pages[number].image, caption="Original — admin only", use_container_width=True)
                            else:
                                st.text(analysis.texts[number])
                            rows = []
                            for entity in entities:
                                row = dict(text=entity["text"], label=entity["label"])
                                if analysis.pages:
                                    row.update(zip(["top", "left", "bottom", "right"], entity["box_2d"]))
                                rows.append(row)
                            columns = ["text", "label"] + (["top", "left", "bottom", "right"] if analysis.pages else [])
                            import pandas as pd
                            edited = st.data_editor(pd.DataFrame(rows, columns=columns), num_rows="dynamic", key=f"entities_{fingerprint}_{number}", hide_index=True)
                            revised = []
                            for row in edited.to_dict("records"):
                                if not row.get("text") or not row.get("label"):
                                    continue
                                entity = dict(text=str(row["text"]), label=str(row["label"]))
                                if analysis.pages:
                                    entity["box_2d"] = [row.get(k) for k in ["top", "left", "bottom", "right"]]
                                revised.append(entity)
                            analysis.detections[number] = revised
                    edit_fingerprint = hashlib.sha256(json.dumps(analysis.detections, sort_keys=True).encode()).hexdigest()
                    if st.session_state.get("preview_fingerprint") != edit_fingerprint:
                        st.session_state.pop("preview", None)
                        st.session_state["approval"] = False
                    if st.button("Preview clean report"):
                        try:
                            st.session_state["preview"] = service.preview(token, analysis)
                            st.session_state["preview_fingerprint"] = edit_fingerprint
                            st.session_state["approval"] = False
                        except Exception as exc:
                            st.error(str(exc))
                    preview = st.session_state.get("preview")
                    if preview is not None:
                        st.subheader("Clean copy the doctor will receive")
                        for number, page in enumerate(preview.previews):
                            st.image(page, caption=f"Clean page {number + 1}", use_container_width=True)
                        st.text_area("Clean report text", preview.masked_text, disabled=True)
                        st.checkbox("I reviewed every page and confirmed the identifying fields are removed.", key="approval")
                        if st.button("Share clean report", disabled=not st.session_state.get("approval")):
                            try:
                                reference = service.share(token, analysis, uploaded.name, reviewed=True)
                                for key in ["analysis", "preview", "preview_fingerprint"]:
                                    st.session_state.pop(key, None)
                                st.success(f"Shared report. Reference: {reference}")
                            except Exception as exc:
                                st.error(str(exc))
        with returned_tab:
            returned = {key: value for key, value in service.documents(token).items() if value["status"] == "Reviewed"}
            if not returned:
                st.info("No reports have been returned yet.")
            for reference, record in returned.items():
                with st.expander(record["title"]):
                    st.write(f"Reference: {reference}")
                    st.write("Doctor's notes")
                    st.text(record["doctor_notes"])
                    if st.button("Restore original PHI", key=f"restore_{reference}"):
                        try:
                            restored = service.restore(token, reference)
                            attachment(restored["original"], restored["extension"], f"original_{reference}")
                            st.text_area("Restored report text", restored["text"], key=f"restored_text_{reference}")
                            st.download_button("Download doctor's notes", restored["doctor_notes"], file_name=f"notes_{reference}.txt", key=f"notes_download_{reference}")
                        except Exception as exc:
                            st.error(str(exc))
    else:
        pending = {key: value for key, value in service.documents(token).items() if value["status"] == "Pending Review"}
        if not pending:
            st.info("No reports are waiting for review.")
        for reference, record in pending.items():
            with st.expander(record["title"], expanded=True):
                st.write(f"Reference: {reference}")
                attachment(base64.b64decode(record["file_b64"]), record["file_ext"], reference)
                st.text_area("Report text", record["masked_text"], disabled=True, key=f"masked_{reference}")
                with st.form(f"review_{reference}"):
                    notes = st.text_area("Clinical notes — do not include identifying information")
                    submitted = st.form_submit_button("Return report to admin")
                if submitted:
                    try:
                        service.return_report(token, reference, notes)
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))


if __name__ == "__main__":
    main()
