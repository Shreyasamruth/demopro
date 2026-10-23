from pathlib import Path
from streamlit.testing.v1 import AppTest
from engine.service import DeidService
from tests.test_workflow import FixtureDetector, report
import agents.deid_agent
import streamlit


APP = str(Path(__file__).parents[1] / "app.py")


def test_portals_login_return_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("DEID_DATA_DIR", str(tmp_path))
    service = DeidService(tmp_path)
    service.auth.add_user("admin", "admin-password-for-tests", "admin")
    service.auth.add_user("doctor", "doctor-password-for-tests", "doctor")
    admin = service.auth.login("admin", "admin-password-for-tests")
    raw = b"Patient: Alice\nFindings: glucose 120 mg/dL."
    analysis = service.analyze(admin, raw, "private.txt", FixtureDetector([dict(personal_data=[dict(text="Alice", label="PERSON")])]))
    reference = service.share(admin, analysis, "private.txt", reviewed=True)

    doctor_ui = AppTest.from_file(APP).run()
    assert not doctor_ui.exception
    assert doctor_ui.title[0].value == "Health-DeID sign in"
    assert len(doctor_ui.radio) == 0
    doctor_ui.text_input[0].input("doctor")
    doctor_ui.text_input[1].input("doctor-password-for-tests")
    doctor_ui.button[0].click().run()
    assert not doctor_ui.exception
    assert doctor_ui.title[0].value == "Doctor review portal"
    assert all("Alice" not in area.value for area in doctor_ui.text_area)
    assert all("Restore" not in button.label for button in doctor_ui.button)
    doctor_ui.text_area[1].input("Keep the current dosage.")
    next(b for b in doctor_ui.button if b.label == "Return report to admin").click().run()
    assert not doctor_ui.exception
    assert service.documents(admin)[reference]["status"] == "Reviewed"

    admin_ui = AppTest.from_file(APP).run()
    admin_ui.text_input[0].input("admin")
    admin_ui.text_input[1].input("admin-password-for-tests")
    admin_ui.button[0].click().run()
    assert not admin_ui.exception
    assert admin_ui.title[0].value == "Admin report portal"
    next(b for b in admin_ui.button if b.label == "Restore original PHI").click().run()
    assert not admin_ui.exception
    assert any(area.value == raw.decode() for area in admin_ui.text_area)
    assert any(text.value == "Keep the current dosage." for text in admin_ui.text)
    next(b for b in admin_ui.button if b.label == "Sign out").click().run()
    assert admin_ui.title[0].value == "Health-DeID sign in"
    assert not admin_ui.exception


def test_admin_upload_preview_and_share(tmp_path, monkeypatch, report):
    monkeypatch.setenv("DEID_DATA_DIR", str(tmp_path))
    service = DeidService(tmp_path)
    service.auth.add_user("admin", "admin-password-for-tests", "admin")
    session = service.auth.login("admin", "admin-password-for-tests")
    raw, results = report
    class Upload:
        name = "private-report.pdf"
        def getvalue(self):
            return raw
    # AppTest does not support file-uploader interactions. Inject only that input
    # and the external provider; exercise the real UI, processor and storage.
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **kw: Upload())
    monkeypatch.setattr(agents.deid_agent, "DeidAgent", lambda: FixtureDetector(results))
    app = AppTest.from_file(APP)
    app.session_state["session"] = session
    app.run()
    next(b for b in app.button if b.label == "Scan report and identify PHI").click().run()
    assert not app.exception and not app.error
    next(b for b in app.button if b.label == "Preview masked report").click().run()
    assert not app.exception and not app.error
    assert app.checkbox[0].label == "I reviewed every page and confirmed the PHI is masked."
    assert next(b for b in app.button if b.label == "Share masked report").disabled
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label == "Share masked report").click().run()
    assert not app.exception and not app.error
    assert len(service.documents(session)) == 1
