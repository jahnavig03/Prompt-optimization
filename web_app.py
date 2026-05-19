"""
web_app.py — Prompt Optimization Lab
Flask backend. Run: python web_app.py  →  http://localhost:5001
"""

import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import db
import runner
import report_pdf
from prompt_parser import parse_prompt

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "lab_config.json"

DEFAULT_CONFIG = {
    "openai_api_key": "",
    "openai_model":   "gpt-4.1",
    "bot_id":         "",
    "yellowai_api_key": "",
    "base_url":       "https://nexus.yellow.ai",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    clean = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}
    CONFIG_FILE.write_text(json.dumps(clean, indent=2))


def _require_key() -> tuple[str, str] | None:
    """Return (api_key, model) or None if not configured."""
    cfg = load_config()
    key = cfg.get("openai_api_key", "")
    if not key:
        return None
    return key, cfg.get("openai_model", "gpt-4.1") or "gpt-4.1"


app = Flask(__name__)

# ── Boot ──────────────────────────────────────────────────────────────────────
db.init_db(seed_dir=BASE_DIR)


# ── Config ────────────────────────────────────────────────────────────────────
@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = load_config()
    def mask(v):
        v = v or ""
        return ("•" * max(0, len(v) - 4) + v[-4:]) if len(v) > 4 else "•" * len(v)
    return jsonify({**cfg,
                    "openai_api_key_masked":   mask(cfg["openai_api_key"]),
                    "yellowai_api_key_masked":  mask(cfg["yellowai_api_key"]),
                    "openai_configured":        bool(cfg["openai_api_key"])})


@app.route("/api/config", methods=["POST"])
def api_save_config():
    data = request.get_json() or {}
    cfg  = load_config()
    for k in DEFAULT_CONFIG:
        if k in data:
            cfg[k] = data[k]
    save_config(cfg)
    return jsonify({"ok": True})


# ── Use cases ─────────────────────────────────────────────────────────────────
@app.route("/api/use-cases", methods=["GET"])
def api_list_use_cases():
    return jsonify(db.list_use_cases())


@app.route("/api/use-cases", methods=["POST"])
def api_create_use_case():
    data  = request.get_json() or {}
    name  = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if db.name_exists(name):
        return jsonify({"error": f"An agent named '{name}' already exists."}), 409
    uc_id = db.create_use_case(name)
    return jsonify({"id": uc_id, "name": name})


@app.route("/api/use-cases/<int:uc_id>", methods=["GET"])
def api_get_use_case(uc_id):
    uc = db.get_use_case(uc_id)
    if not uc:
        return jsonify({"error": "not found"}), 404
    return jsonify(uc)


@app.route("/api/use-cases/<int:uc_id>", methods=["PATCH"])
def api_rename_use_case(uc_id):
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if not db.get_use_case(uc_id):
        return jsonify({"error": "not found"}), 404
    if db.name_exists(name, exclude_id=uc_id):
        return jsonify({"error": f"Another agent is already named '{name}'."}), 409
    db.rename_use_case(uc_id, name)
    return jsonify({"id": uc_id, "name": name})


@app.route("/api/use-cases/<int:uc_id>", methods=["DELETE"])
def api_delete_use_case(uc_id):
    db.delete_use_case(uc_id)
    return jsonify({"ok": True})


# ── Trigger ───────────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/trigger", methods=["PUT"])
def api_save_trigger(uc_id):
    data = request.get_json() or {}
    db.save_trigger(uc_id, data.get("trigger", ""))
    return jsonify({"ok": True})


# ── Requirements ──────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/requirements", methods=["PUT"])
def api_save_requirements(uc_id):
    data = request.get_json() or {}
    db.update_requirements(uc_id, data.get("content", ""))
    return jsonify({"ok": True})


# ── Variables ─────────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/variables", methods=["PUT"])
def api_save_variables(uc_id):
    data = request.get_json() or {}
    db.replace_variables(uc_id, data.get("sub_agents", []), data.get("memory_keys", []))
    return jsonify({"ok": True})


# ── Tools ─────────────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/tools", methods=["PUT"])
def api_save_tools(uc_id):
    data = request.get_json() or {}
    db.replace_tools(uc_id, data.get("tools", []))
    return jsonify({"ok": True})


# ── Rich Media ────────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/rich-media", methods=["PUT"])
def api_save_rich_media(uc_id):
    data = request.get_json() or {}
    db.replace_rich_media(uc_id, data.get("rich_media", []))
    return jsonify({"ok": True})


# ── Prompt ────────────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/prompt", methods=["PUT"])
def api_save_prompt(uc_id):
    data           = request.get_json() or {}
    create_version = bool(data.get("create_version", False))
    label          = data.get("label", "")
    result         = db.save_prompt(uc_id, data.get("content", ""), create_version=create_version, label=label)
    return jsonify(result)


@app.route("/api/use-cases/<int:uc_id>/prompt/parse", methods=["POST"])
def api_parse_prompt(uc_id):
    uc = db.get_use_case(uc_id)
    if not uc:
        return jsonify({"error": "not found"}), 404
    data    = request.get_json() or {}
    content = data.get("content", uc["prompt"]["content"])
    result  = parse_prompt(content, uc["tools"], uc["sub_agents"], uc["memory_keys"])
    return jsonify(result)


@app.route("/api/use-cases/<int:uc_id>/prompt/versions", methods=["GET"])
def api_prompt_versions(uc_id):
    return jsonify(db.get_prompt_versions(uc_id))


@app.route("/api/use-cases/<int:uc_id>/prompt/versions/<int:version>", methods=["GET"])
def api_prompt_version(uc_id, version):
    p = db.get_prompt_by_version(uc_id, version)
    if not p:
        return jsonify({"error": "not found"}), 404
    return jsonify(p)


@app.route("/api/use-cases/<int:uc_id>/prompt/diff", methods=["POST"])
def api_prompt_diff(uc_id):
    data = request.json or {}
    a = data.get("a"); b = data.get("b")
    pa = db.get_prompt_by_version(uc_id, int(a)) if a is not None else None
    pb = db.get_prompt_by_version(uc_id, int(b)) if b is not None else None
    if not pa or not pb:
        return jsonify({"error": "version not found"}), 404
    return jsonify({
        "a": {"version": pa.get("version"), "content": pa.get("content", ""), "created_at": pa.get("created_at")},
        "b": {"version": pb.get("version"), "content": pb.get("content", ""), "created_at": pb.get("created_at")},
        "diff": runner.compute_diff(pa.get("content", ""), pb.get("content", "")),
    })


# ── Tests ─────────────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/tests", methods=["GET"])
def api_get_tests(uc_id):
    return jsonify(db.get_tests(uc_id))


@app.route("/api/use-cases/<int:uc_id>/generate-tests", methods=["POST"])
def api_generate_tests(uc_id):
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured. Open ⚙ Settings."}), 400

    uc = db.get_use_case(uc_id)
    if not uc:
        return jsonify({"error": "use case not found"}), 404
    if not uc["requirements"].strip():
        return jsonify({"error": "Requirements are empty. Write your requirements first."}), 400

    from openai import OpenAI
    api_key, model = creds
    try:
        current_prompt = uc.get("prompt", {}).get("content", "") or ""
        tests = runner.generate_tests(
            uc["requirements"], uc["sub_agents"], uc["memory_keys"], uc["tools"],
            OpenAI(api_key=api_key), model,
            prompt=current_prompt,
        )
        normalized = [
            {
                "test_id":             t.get("test_id") or t.get("id", f"TC-{i+1:03d}"),
                "name":                t.get("name", ""),
                "conversation_script": t.get("conversation_script", []),
                "pass_criteria":       t.get("pass_criteria", []),
                "mock_overrides":      t.get("mock_overrides", {}),
            }
            for i, t in enumerate(tests)
        ]
        db.replace_tests(uc_id, normalized)
        return jsonify({"tests": normalized, "count": len(normalized)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Run a single test (no optimization) ──────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/run", methods=["POST"])
def api_run_tests(uc_id):
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured."}), 400

    data   = request.get_json() or {}
    tc_ids = data.get("ids")  # None = all

    for r in db.list_runs(uc_id):
        if r["status"] in ("running", "paused") and runner.get_queue(r["id"]) is not None:
            return jsonify({
                "error": "A run is already in progress for this use case.",
                "active_run_id": r["id"],
                "status": r["status"],
            }), 409

    uc    = db.get_use_case(uc_id)
    tests = db.get_tests(uc_id)
    if tc_ids:
        tests = [t for t in tests if t["test_id"] in tc_ids]

    run_id = db.create_run(uc_id, "manual", len(tests), max_iterations=1)
    eq     = runner.register_run(run_id)

    api_key, model = creds
    from openai import OpenAI

    def _bg():
        try:
            client        = OpenAI(api_key=api_key)
            openai_tools  = runner.build_openai_tools(uc["tools"])
            system_prompt = runner.build_system_prompt(uc["prompt"]["content"])

            eq.put({"type": "run_start", "run_id": run_id, "total_tests": len(tests), "max_iterations": 1})
            eq.put({"type": "iteration_start", "run_id": run_id, "n": 1})
            for tc in tests:
                if runner._should_stop(run_id):
                    break
                tc_id = tc["test_id"]
                eq.put({"type": "test_start", "tc_id": tc_id, "name": tc["name"], "run_id": run_id})
                result = runner.run_conversation(
                    tc_id, tc["conversation_script"], tc["mock_overrides"],
                    system_prompt, openai_tools, client, model, eq, run_id=run_id
                )
                if runner._should_stop(run_id):
                    break
                eq.put({"type": "eval_start", "tc_id": tc_id})
                ev = runner.evaluate_transcript(result, tc["pass_criteria"], client, model)
                eq.put({"type": "test_complete",
                        "tc_id": tc_id, "name": tc["name"],
                        "overall": ev.get("overall", "ERROR"),
                        "summary": ev.get("summary", ""),
                        "results": ev.get("results", []),
                        "criteria": ev.get("criteria", []),
                        "transcript": result["transcript"],
                        "tool_calls_made": result.get("tool_calls_made", {})})
        except Exception as e:
            eq.put({"type": "error", "message": str(e)})
        finally:
            final_status = "stopped" if runner._should_stop(run_id) else "done"
            eq.put({"type": "done", "run_id": run_id})
            db.update_run(run_id, status=final_status, ended_at="datetime('now')")
            runner._stop_flags.discard(run_id)

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"run_id": run_id})


# ── Optimization ──────────────────────────────────────────────────────────────
@app.route("/api/use-cases/<int:uc_id>/optimize", methods=["POST"])
def api_optimize(uc_id):
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured."}), 400

    data     = request.get_json() or {}
    mode     = data.get("mode", "auto")       # "auto" | "step"
    max_iter = int(data.get("max_iterations", 10))

    for r in db.list_runs(uc_id):
        if r["status"] in ("running", "paused") and runner.get_queue(r["id"]) is not None:
            return jsonify({
                "error": "A run is already in progress for this use case.",
                "active_run_id": r["id"],
                "status": r["status"],
            }), 409

    tests  = db.get_tests(uc_id)
    run_id = db.create_run(uc_id, mode, len(tests), max_iterations=max_iter)
    runner.register_run(run_id)

    threading.Thread(target=runner.run_optimization, args=(run_id, uc_id), daemon=True).start()
    return jsonify({"run_id": run_id})


@app.route("/api/runs/<int:run_id>/continue", methods=["POST"])
def api_run_continue(run_id):
    runner.signal_continue(run_id)
    return jsonify({"ok": True})


@app.route("/api/runs/<int:run_id>/stop", methods=["POST"])
def api_run_stop(run_id):
    runner.signal_stop(run_id)
    return jsonify({"ok": True})


@app.route("/api/runs/<int:run_id>", methods=["GET"])
def api_get_run(run_id):
    run = db.get_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify(run)


@app.route("/api/use-cases/<int:uc_id>/runs", methods=["GET"])
def api_list_runs(uc_id):
    runs = db.list_runs(uc_id)
    for r in runs:
        r["iterations"] = db.get_iterations(r["id"])
    return jsonify(runs)


# ── SSE stream ────────────────────────────────────────────────────────────────
@app.route("/api/stream/<int:run_id>")
def api_stream(run_id):
    def generate():
        eq = runner.get_queue(run_id)
        if not eq:
            yield f"data: {json.dumps({'type':'error','message':'run not found'})}\n\n"
            return
        import queue as Q
        while True:
            try:
                event = eq.get(timeout=90)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
            except Q.Empty:
                yield 'data: {"type":"ping"}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Playwright live-bot testing ───────────────────────────────────────────────
_pw_queues:     dict[int, queue.Queue] = {}
_pw_stop_flags: set[int]              = set()
_pw_otp_queues: dict[int, queue.Queue] = {}


@app.route("/live-bot")
def live_bot():
    return render_template("live_bot.html")


@app.route("/api/playwright/use-cases", methods=["GET"])
def api_pw_use_cases():
    return jsonify(db.list_use_cases())


@app.route("/api/playwright/run", methods=["POST"])
def api_playwright_run():
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured. Open ⚙ Settings in the Lab."}), 400

    data   = request.get_json() or {}
    uc_ids = [int(x) for x in data.get("use_case_ids", [])]
    mode   = data.get("mode", "headless")  # "headless" | "browser"
    cred_email = (data.get("email") or "").strip()
    cred_phone = (data.get("phone") or "").strip()

    if not uc_ids:
        return jsonify({"error": "Select at least one agent."}), 400

    cfg    = load_config()
    bot_id = cfg.get("bot_id", "").strip()
    if not bot_id:
        return jsonify({"error": "Bot ID not configured. Open ⚙ Settings in the Lab."}), 400

    try:
        import playwright_runner as pw_runner  # noqa: F401  (verify it imports cleanly)
    except ImportError:
        return jsonify({"error": "Playwright is not installed. Run: pip install playwright && playwright install chromium"}), 500

    api_key, model = creds
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    # Generate tests for each selected use case
    all_tests: list[dict] = []
    single_agent_name: str | None = None
    for uc_id in uc_ids:
        uc = db.get_use_case(uc_id)
        if not uc:
            continue
        try:
            raw = runner.generate_playwright_tests(
                uc["requirements"], uc["sub_agents"], uc["tools"],
                uc["prompt"]["content"], client, model,
                email=cred_email, phone=cred_phone,
                trigger=uc.get("trigger", ""),
            )
        except Exception as e:
            return jsonify({"error": f"Test generation failed for '{uc['name']}': {e}"}), 500

        for i, t in enumerate(raw):
            t["test_id"]       = t.get("test_id") or f"PT-{len(all_tests) + i + 1:03d}"
            t["use_case_id"]   = uc_id
            t["use_case_name"] = uc["name"]
        all_tests.extend(raw)

    if not all_tests:
        return jsonify({"error": "No tests were generated. Add requirements to the selected agents."}), 400

    # Single-agent scope: when exactly one agent is selected, lock execution
    # to that agent — any [ROUTE TO: Other] event will fail the test.
    if len(uc_ids) == 1:
        uc = db.get_use_case(uc_ids[0])
        if uc:
            single_agent_name = uc["name"]

    # Cross-agent context: collect prompts for ALL agents NOT under test so the
    # evaluator can correctly judge silent Yellow.ai routing handoffs.
    other_agent_context: dict[str, str] = {}
    for other_uc_stub in db.list_use_cases():
        if other_uc_stub["id"] in uc_ids:
            continue  # skip agents being tested
        full_uc = db.get_use_case(other_uc_stub["id"])
        if not full_uc:
            continue
        prompt_text = (full_uc.get("prompt") or {}).get("content", "").strip()
        if prompt_text:
            other_agent_context[other_uc_stub["name"]] = prompt_text

    run_id = db.create_playwright_run(uc_ids, mode, bot_id, len(all_tests))
    eq     = queue.Queue()
    otp_q  = queue.Queue()
    _pw_queues[run_id]     = eq
    _pw_otp_queues[run_id] = otp_q

    eq.put({"type": "pw_run_start", "run_id": run_id,
            "total_tests": len(all_tests), "mode": mode, "tests": all_tests,
            "single_agent_name": single_agent_name})

    def _bg():
        try:
            pw_runner.start_playwright_run(
                run_id, all_tests, bot_id, mode, eq, _pw_stop_flags, client, model,
                otp_queue=otp_q,
                single_agent_name=single_agent_name,
                other_agent_context=other_agent_context or None,
            )
        except Exception as exc:
            eq.put({"type": "error", "message": str(exc)})
            eq.put({"type": "pw_done", "run_id": run_id})
        finally:
            _pw_stop_flags.discard(run_id)
            _pw_otp_queues.pop(run_id, None)
            db.update_playwright_run(run_id, status="done",
                                     ended_at=datetime.now().isoformat())

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"run_id": run_id, "total_tests": len(all_tests), "tests": all_tests})


@app.route("/api/playwright/stream/<int:run_id>")
def api_playwright_stream(run_id):
    def generate():
        eq = _pw_queues.get(run_id)
        if not eq:
            yield f"data: {json.dumps({'type':'error','message':'run not found'})}\n\n"
            return
        import queue as Q
        while True:
            try:
                event = eq.get(timeout=90)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "pw_done":
                    break
            except Q.Empty:
                yield 'data: {"type":"ping"}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/playwright/runs", methods=["GET"])
def api_playwright_list_runs():
    runs = db.list_playwright_runs()
    for r in runs:
        r["results"] = db.get_playwright_results(r["id"])
    return jsonify(runs)


@app.route("/api/playwright/runs/<int:run_id>", methods=["GET"])
def api_playwright_get_run(run_id):
    run = db.get_playwright_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    run["results"] = db.get_playwright_results(run_id)
    return jsonify(run)


@app.route("/api/playwright/stop/<int:run_id>", methods=["POST"])
def api_playwright_stop(run_id):
    _pw_stop_flags.add(run_id)
    db.update_playwright_run(run_id, status="stopped",
                             ended_at=datetime.now().isoformat())
    return jsonify({"ok": True})


@app.route("/api/playwright/runs/<int:run_id>", methods=["DELETE"])
def api_playwright_delete(run_id):
    db.delete_playwright_run(run_id)
    return jsonify({"ok": True})


@app.route("/api/playwright/runs/<int:run_id>/report", methods=["GET"])
def api_playwright_report(run_id):
    try:
        pdf_bytes = report_pdf.generate_report(run_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Report generation failed: {e}"}), 500
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=run_{run_id}_report.pdf"},
    )


@app.route("/api/playwright/detect-credentials", methods=["POST"])
def api_detect_credentials():
    data   = request.get_json() or {}
    uc_ids = [int(x) for x in data.get("use_case_ids", [])]
    result = {"needs_email": False, "needs_phone": False, "needs_otp": False}
    for uc_id in uc_ids:
        uc = db.get_use_case(uc_id)
        if not uc:
            continue
        prompt_text = (uc.get("prompt") or {}).get("content", "")
        req_text    = uc.get("requirements", "")
        needs = runner.detect_credential_needs(prompt_text + "\n" + req_text)
        for k in result:
            result[k] = result[k] or needs[k]
    return jsonify(result)


@app.route("/api/playwright/otp/<int:run_id>", methods=["POST"])
def api_playwright_otp(run_id):
    data = request.get_json() or {}
    otp  = data.get("otp", "").strip()
    if not otp:
        return jsonify({"error": "OTP is required"}), 400
    otp_q = _pw_otp_queues.get(run_id)
    if not otp_q:
        return jsonify({"error": "No active OTP wait for this run"}), 404
    otp_q.put(otp)
    return jsonify({"ok": True})


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.jinja_env.auto_reload = True          # always serve latest templates
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    print(f"Starting Prompt Optimization Lab on http://localhost:{port}")
    app.run(debug=False, port=port, threaded=True)
