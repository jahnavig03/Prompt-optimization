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
import knowledge_store
import runner
import model_guides
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


@app.route("/api/models", methods=["GET"])
def api_list_models():
    return jsonify(model_guides.list_models())


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


def _estimate_tokens(text: str) -> int:
    """Approximate token count (cl100k_base when tiktoken is available)."""
    if not text:
        return 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, (len(text) + 3) // 4)


@app.route("/api/use-cases/<int:uc_id>/prompt/diff", methods=["POST"])
def api_prompt_diff(uc_id):
    data = request.json or {}
    a = data.get("a"); b = data.get("b")
    pa = db.get_prompt_by_version(uc_id, int(a)) if a is not None else None
    pb = db.get_prompt_by_version(uc_id, int(b)) if b is not None else None
    if not pa or not pb:
        return jsonify({"error": "version not found"}), 404
    cfg = load_config()
    model = cfg.get("openai_model", "gpt-4.1") or "gpt-4.1"
    content_a = pa.get("content", "") or ""
    content_b = pb.get("content", "") or ""
    return jsonify({
        "a": {
            "version": pa.get("version"), "content": content_a,
            "created_at": pa.get("created_at"),
            "lines": len(content_a.splitlines()) or (1 if content_a else 0),
            "tokens": _estimate_tokens(content_a),
        },
        "b": {
            "version": pb.get("version"), "content": content_b,
            "created_at": pb.get("created_at"),
            "lines": len(content_b.splitlines()) or (1 if content_b else 0),
            "tokens": _estimate_tokens(content_b),
        },
        "model": model,
        "diff": runner.compute_diff(content_a, content_b),
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
            use_case_id=uc_id,
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
    _, default_model = creds
    selected = (data.get("model") or default_model or "gpt-4.1").strip()
    api_model, _ = model_guides.resolve_api_model(selected, default_model)

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

    run_id = db.create_run(uc_id, "manual", len(tests), max_iterations=1, model=selected)
    eq     = runner.register_run(run_id)

    api_key, _ = creds
    model = api_model
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
                if runner._should_stop(run_id):
                    break
                eq.put({"type": "eval_start", "tc_id": tc_id})
                if runner._should_stop(run_id):
                    break
                ev = runner.evaluate_transcript(result, tc["pass_criteria"], client, model,
                                                  use_case_id=uc_id, test_id=tc_id)
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
            eq.put({"type": "done", "run_id": run_id, "stopped": final_status == "stopped"})
            db.update_run(run_id, status=final_status, ended_at=datetime.now().isoformat())
            runner._stop_flags.discard(run_id)
            # ── Auto-update knowledge from simulated run results ──
            _auto_update_knowledge_from_sim_run(run_id, uc_id)

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
    _, default_model = creds
    selected = (data.get("model") or default_model or "gpt-4.1").strip()
    api_model, _ = model_guides.resolve_api_model(selected, default_model)

    for r in db.list_runs(uc_id):
        if r["status"] in ("running", "paused") and runner.get_queue(r["id"]) is not None:
            return jsonify({
                "error": "A run is already in progress for this use case.",
                "active_run_id": r["id"],
                "status": r["status"],
            }), 409

    tests  = db.get_tests(uc_id)
    run_id = db.create_run(uc_id, mode, len(tests), max_iterations=max_iter, model=selected)
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
                use_case_id=uc_id,
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
        import sys, traceback
        try:
            pw_runner.start_playwright_run(
                run_id, all_tests, bot_id, mode, eq, _pw_stop_flags, client, model,
                otp_queue=otp_q,
                single_agent_name=single_agent_name,
                other_agent_context=other_agent_context or None,
            )
        except BaseException as exc:
            tb = traceback.format_exc()
            print(f"[web_app._bg] crash run {run_id}: {type(exc).__name__}: {exc}\n{tb}",
                  flush=True, file=sys.stderr)
            try:
                eq.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                eq.put({"type": "pw_done", "run_id": run_id})
            except Exception:
                pass
        finally:
            _pw_stop_flags.discard(run_id)
            _pw_otp_queues.pop(run_id, None)
            current = db.get_playwright_run(run_id)
            if not current or current.get("status") != "stopped":
                db.update_playwright_run(run_id, status="done",
                                         ended_at=datetime.now().isoformat())
            # ── Auto-update knowledge: merge PASS results → rebuild rubric ──
            _auto_update_knowledge_from_pw_run(run_id, uc_ids, bot_id)

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
    eq = _pw_queues.get(run_id)
    if eq:
        try:
            eq.put({"type": "pw_stopping", "run_id": run_id})
        except Exception:
            pass
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


# ── Acceptance rules (human overrides for LLM strictness) ────────────────────
@app.route("/api/use-cases/<int:uc_id>/acceptance-rules", methods=["GET"])
def api_list_acceptance_rules(uc_id):
    return jsonify(db.list_acceptance_rules(uc_id))


@app.route("/api/use-cases/<int:uc_id>/acceptance-rules", methods=["POST"])
def api_create_acceptance_rule(uc_id):
    data = request.get_json() or {}
    rule = (data.get("human_rule") or "").strip()
    if not rule:
        return jsonify({"error": "human_rule is required"}), 400
    conv_turns = data.get("conversation_turns", [])
    rule_id = db.create_acceptance_rule(
        uc_id, rule,
        criterion=data.get("criterion", ""),
        llm_reason=data.get("llm_reason", ""),
        actual_response=data.get("actual_response", ""),
        scope=data.get("scope", "agent"),
        source=data.get("source", ""),
        source_run_id=data.get("source_run_id"),
        source_test_id=data.get("source_test_id", ""),
        conversation_turns=json.dumps(conv_turns) if isinstance(conv_turns, list) else str(conv_turns),
    )
    return jsonify({"id": rule_id})


@app.route("/api/acceptance-rules/<int:rule_id>", methods=["PATCH"])
def api_update_acceptance_rule(rule_id):
    data = request.get_json() or {}
    fields = {}
    if "human_rule" in data:
        fields["human_rule"] = (data["human_rule"] or "").strip()
    if "scope" in data and data["scope"] in ("agent", "test"):
        fields["scope"] = data["scope"]
    if "active" in data:
        fields["active"] = 1 if data["active"] else 0
    db.update_acceptance_rule(rule_id, **fields)
    return jsonify({"ok": True})


@app.route("/api/acceptance-rules/<int:rule_id>", methods=["DELETE"])
def api_delete_acceptance_rule(rule_id):
    db.delete_acceptance_rule(rule_id)
    return jsonify({"ok": True})


@app.route("/api/acceptance-rules/suggest", methods=["POST"])
def api_suggest_acceptance_rule():
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured."}), 400
    data = request.get_json() or {}
    api_key, model = creds
    from openai import OpenAI
    suggestion = runner.suggest_acceptance_rule(
        criterion=data.get("criterion", ""),
        llm_reason=data.get("llm_reason", ""),
        actual_response=data.get("actual_response", ""),
        client=OpenAI(api_key=api_key), model=model,
    )
    return jsonify({"suggestion": suggestion})


# ── Knowledge base (accepted tests + derived rubric / SOP) ───────────────────
#
# Per-agent knowledge lives at: knowledge/agent_<uc_id>/
#   accepted_tests.json   ← uploaded JSON (or PDF→JSON converted) of passing runs
#   rubric.md             ← LLM-distilled SOP, rebuilt after every change
#
# The rubric is injected into every evaluator prompt (simulation + Playwright)
# so judgement stays consistent across runs and across bots.

def _auto_update_knowledge_from_pw_run(run_id: int, uc_ids: list[int], bot_id: str) -> None:
    """After a Playwright run completes, extract PASS results per agent,
    merge into their knowledge store, and rebuild the rubric automatically."""
    for uc_id in uc_ids:
        try:
            uc = db.get_use_case(uc_id)
            agent_name = uc["name"] if uc else ""
            payload = knowledge_store.export_run_as_payload(
                uc_id, run_id, agent_name, bot_id, accepted_only=True
            )
            passed_tests = payload.get("accepted_tests", [])
            if not passed_tests:
                print(f"[knowledge] run {run_id}: no PASS results for uc={uc_id}, skipping")
                continue
            knowledge_store.save_accepted_tests(uc_id, payload)
            print(f"[knowledge] run {run_id}: merged {len(passed_tests)} PASS tests for uc={uc_id}")
            _rebuild_rubric_async(uc_id)
        except Exception as e:
            print(f"[knowledge] auto-update failed for uc={uc_id} run={run_id}: {e}")


def _auto_update_knowledge_from_sim_run(run_id: int, uc_id: int) -> None:
    """After a simulated run completes, extract PASS results,
    merge into knowledge store, and rebuild rubric."""
    try:
        uc = db.get_use_case(uc_id)
        agent_name = uc["name"] if uc else ""
        bot_id = uc.get("bot_id", "") if uc else ""
        iters = db.get_iterations(run_id)
        if not iters:
            return
        last_iter = iters[-1]
        results = last_iter.get("results", [])
        tests = []
        for r in results:
            if (r.get("overall") or "").upper() != "PASS":
                continue
            transcript = r.get("transcript", [])
            turns = []
            user_msg, expected = "", ""
            for entry in transcript:
                role = entry.get("role", "")
                content = entry.get("content", "")
                if role == "user":
                    user_msg = content
                elif role == "assistant" and user_msg:
                    turns.append({"user": user_msg, "expected": expected, "bot": content})
                    user_msg, expected = "", ""
            if turns:
                tests.append({
                    "id": r.get("tc_id", ""),
                    "name": r.get("name", ""),
                    "judge": (r.get("summary") or "").strip(),
                    "turns": turns,
                })
        if not tests:
            print(f"[knowledge] sim run {run_id}: no PASS results for uc={uc_id}, skipping")
            return
        payload = {
            "schema_version": 1,
            "agent": agent_name,
            "bot_id": bot_id,
            "use_case_id": uc_id,
            "exported_at": datetime.now().isoformat(),
            "accepted_tests": tests,
        }
        knowledge_store.save_accepted_tests(uc_id, payload)
        print(f"[knowledge] sim run {run_id}: merged {len(tests)} PASS tests for uc={uc_id}")
        _rebuild_rubric_async(uc_id)
    except Exception as e:
        print(f"[knowledge] sim auto-update failed for uc={uc_id} run={run_id}: {e}")


def _rebuild_rubric_async(uc_id: int) -> None:
    """Fire-and-forget rubric rebuild — runs in a thread so the request stays fast."""
    creds = _require_key()
    if not creds:
        print(f"[knowledge] skipping rubric rebuild for uc={uc_id}: no OpenAI key")
        return
    api_key, model = creds
    def _run():
        try:
            from openai import OpenAI
            knowledge_store.rebuild_rubric(uc_id, OpenAI(api_key=api_key), model)
        except Exception as e:
            print(f"[knowledge] rubric rebuild failed for uc={uc_id}: {e}")
    threading.Thread(target=_run, daemon=True).start()


@app.route("/api/use-cases/<int:uc_id>/knowledge", methods=["GET"])
def api_get_knowledge(uc_id):
    doc    = knowledge_store.load_accepted_tests(uc_id)
    rubric = knowledge_store.load_rubric(uc_id)
    return jsonify({
        "accepted_tests_count": len(doc.get("accepted_tests", [])),
        "agent":                doc.get("agent", ""),
        "bot_id":               doc.get("bot_id", ""),
        "exported_at":          doc.get("exported_at", ""),
        "rubric":               rubric,
        "has_rubric":           bool(rubric.strip()),
    })


@app.route("/api/use-cases/<int:uc_id>/knowledge/upload", methods=["POST"])
def api_upload_knowledge(uc_id):
    """
    Accepts a JSON file (multipart upload OR raw JSON body) of accepted tests.
    Merges with existing knowledge (dedupe by test id).  Triggers rubric rebuild.
    Reject anything that isn't valid JSON in the expected schema.
    """
    payload = None
    # Multipart file upload
    if "file" in request.files:
        f = request.files["file"]
        name = (f.filename or "").lower()
        if name.endswith(".pdf"):
            # Convert PDF to JSON in-memory
            import tempfile
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(f.read())
                    tmp_path = tmp.name
                payload = knowledge_store.parse_lab_pdf(tmp_path)
                os.unlink(tmp_path)
                if not payload.get("accepted_tests"):
                    return jsonify({"error": "No PASS test cases found in PDF."}), 400
            except Exception as e:
                return jsonify({"error": f"PDF parsing failed: {e}"}), 400
        elif name.endswith(".json"):
            try:
                payload = json.loads(f.read().decode("utf-8"))
            except Exception as e:
                return jsonify({"error": f"Invalid JSON: {e}"}), 400
        else:
            return jsonify({"error": "Only .json or .pdf files are accepted."}), 400
    else:
        payload = request.get_json(silent=True)

    if payload is None:
        return jsonify({"error": "No JSON payload provided."}), 400

    mode = request.args.get("mode", "merge")    # "merge" (default) | "replace"
    try:
        if mode == "replace":
            doc = knowledge_store.replace_accepted_tests(uc_id, payload)
        else:
            doc = knowledge_store.save_accepted_tests(uc_id, payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    _rebuild_rubric_async(uc_id)
    return jsonify({
        "ok": True,
        "accepted_tests_count": len(doc.get("accepted_tests", [])),
        "rubric_rebuild": "scheduled",
    })


@app.route("/api/use-cases/<int:uc_id>/knowledge/rebuild", methods=["POST"])
def api_rebuild_rubric(uc_id):
    """Force a synchronous rubric rebuild and return the result."""
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured."}), 400
    api_key, model = creds
    from openai import OpenAI
    text = knowledge_store.rebuild_rubric(uc_id, OpenAI(api_key=api_key), model)
    return jsonify({"rubric": text, "has_rubric": bool(text.strip())})


@app.route("/api/use-cases/<int:uc_id>/knowledge", methods=["DELETE"])
def api_clear_knowledge(uc_id):
    knowledge_store.clear(uc_id)
    return jsonify({"ok": True})


@app.route("/api/runs/<int:run_id>/export-accepted", methods=["GET"])
def api_export_run_accepted(run_id):
    """
    Export a Playwright run as the accepted-tests JSON shape.  Only PASS rows
    by default; pass ?include=all to include FAIL/ERROR too (caller's call).
    """
    run = db.get_run(run_id)
    if not run:
        return jsonify({"error": "run not found"}), 404
    uc_id   = run.get("use_case_id")
    uc      = db.get_use_case(uc_id) if uc_id else None
    cfg     = load_config()
    agent   = (uc or {}).get("name", "")
    bot_id  = cfg.get("bot_id", "")
    include = request.args.get("include", "passed")
    payload = knowledge_store.export_run_as_payload(
        uc_id, run_id, agent, bot_id, accepted_only=(include != "all"),
    )
    fname = f"accepted_tests_run_{run_id}.json"
    return Response(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Hook: every acceptance-rule create/update/delete kicks off a rubric rebuild
# so the SOP saturates as the user clicks "LLM was wrong — accept this".
_orig_create_acceptance_rule = api_create_acceptance_rule
_orig_update_acceptance_rule = api_update_acceptance_rule
_orig_delete_acceptance_rule = api_delete_acceptance_rule


def _wrap_with_rebuild(view, get_uc_id):
    def wrapped(*args, **kwargs):
        resp = view(*args, **kwargs)
        try:
            uc_id = get_uc_id(*args, **kwargs)
            if uc_id:
                _rebuild_rubric_async(int(uc_id))
        except Exception:
            pass
        return resp
    wrapped.__name__ = view.__name__
    return wrapped


# Replace the registered view functions with the wrapped versions
app.view_functions["api_create_acceptance_rule"] = _wrap_with_rebuild(
    _orig_create_acceptance_rule, lambda uc_id: uc_id
)
app.view_functions["api_update_acceptance_rule"] = _wrap_with_rebuild(
    _orig_update_acceptance_rule,
    lambda rule_id: (db.get_acceptance_rule(rule_id) or {}).get("use_case_id"),
)
app.view_functions["api_delete_acceptance_rule"] = _wrap_with_rebuild(
    _orig_delete_acceptance_rule,
    lambda rule_id: (db.get_acceptance_rule(rule_id) or {}).get("use_case_id"),
)


# ── Custom Testing ───────────────────────────────────────────────────────────
_custom_queues:     dict[int, queue.Queue] = {}
_custom_stop_flags: set[int]              = set()


@app.route("/custom")
def custom_page():
    return render_template("custom.html")


@app.route("/api/custom/tests/<int:uc_id>", methods=["GET"])
def api_custom_tests(uc_id):
    return jsonify(db.list_custom_tests(uc_id))


@app.route("/api/custom/tests/<int:uc_id>", methods=["POST"])
def api_custom_test_create(uc_id):
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    desc = data.get("description", "")
    steps = data.get("steps", [])
    existing = db.list_custom_tests(uc_id)
    next_num = len(existing) + 1
    test_id = f"CT-{next_num:03d}"
    row_id = db.add_custom_test(uc_id, test_id, name, desc, steps)
    return jsonify({"id": row_id, "test_id": test_id})


@app.route("/api/custom/tests/<int:uc_id>/generate", methods=["POST"])
def api_custom_test_generate(uc_id):
    """Generate test steps from a description using LLM."""
    creds = _require_key()
    if not creds:
        return jsonify({"error": "OpenAI API key not configured."}), 400
    data = request.get_json() or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description required"}), 400

    uc = db.get_use_case(uc_id)
    if not uc:
        return jsonify({"error": "use case not found"}), 404

    api_key, model = creds
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    trigger = uc.get("trigger", "")
    prompt_text = (uc.get("prompt") or {}).get("content", "")

    system = (
        "You are a QA engineer generating test steps for a Yellow.ai chatbot.\n"
        "Given a description of what to test, generate a JSON array of test step objects.\n"
        "Each step has:\n"
        '  - "action": either "send" (send a text message) or "click" (click a button)\n'
        '  - "value": the text to send or button label to click\n'
        "Return ONLY the JSON array, no markdown fences.\n"
        "The first step should be the trigger message that starts the conversation."
    )
    user_msg = f"Agent trigger: {trigger}\nAgent prompt summary (first 500 chars): {prompt_text[:500]}\n\nTest description:\n{description}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
        steps = json.loads(raw)
        return jsonify({"steps": steps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/custom/tests/item/<int:test_db_id>", methods=["PATCH"])
def api_custom_test_update(test_db_id):
    data = request.get_json() or {}
    db.update_custom_test(
        test_db_id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        steps=data.get("steps", []),
    )
    return jsonify({"ok": True})


@app.route("/api/custom/tests/item/<int:test_db_id>", methods=["DELETE"])
def api_custom_test_delete(test_db_id):
    db.delete_custom_test(test_db_id)
    return jsonify({"ok": True})


@app.route("/api/custom/run", methods=["POST"])
def api_custom_run():
    data   = request.get_json() or {}
    uc_id  = int(data.get("use_case_id", 0))
    if not uc_id:
        return jsonify({"error": "use_case_id required"}), 400

    uc = db.get_use_case(uc_id)
    if not uc:
        return jsonify({"error": "use case not found"}), 404

    tests = db.list_custom_tests(uc_id)
    test_ids = data.get("test_ids")
    if test_ids:
        tests = [t for t in tests if t["test_id"] in test_ids]
    if not tests:
        return jsonify({"error": "No custom tests to run."}), 400

    cfg    = load_config()
    bot_id = cfg.get("bot_id", "").strip()
    if not bot_id:
        return jsonify({"error": "Bot ID not configured."}), 400

    try:
        import playwright_runner as pw_runner  # noqa
    except ImportError:
        return jsonify({"error": "Playwright not installed."}), 500

    run_id = db.create_custom_run(uc_id, bot_id, len(tests))
    eq     = queue.Queue()
    _custom_queues[run_id] = eq

    pw_tests = []
    for t in tests:
        turns = []
        for step in t.get("steps", []):
            action = step.get("action", "send")
            value = step.get("value", "")
            if action == "click":
                turns.append({"user": f"[Click {value}]", "expected": ""})
            else:
                turns.append({"user": value, "expected": ""})
        pw_tests.append({
            "test_id": t["test_id"],
            "name": t["name"],
            "turns": turns,
            "use_case_id": uc_id,
            "use_case_name": uc["name"],
            "pass_criteria": [],
            "category": "custom",
        })

    eq.put({"type": "pw_run_start", "run_id": run_id,
            "total_tests": len(pw_tests), "mode": "headless", "tests": pw_tests})

    def _bg():
        import sys, traceback
        try:
            pw_runner.start_playwright_run(
                run_id, pw_tests, bot_id, "headless", eq, _custom_stop_flags,
                client=None, model=None,
                single_agent_name=uc["name"],
                skip_evaluation=True,
            )
        except BaseException as exc:
            tb = traceback.format_exc()
            print(f"[custom._bg] crash run {run_id}: {type(exc).__name__}: {exc}\n{tb}",
                  flush=True, file=sys.stderr)
            try:
                eq.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                eq.put({"type": "pw_done", "run_id": run_id})
            except Exception:
                pass
        finally:
            _custom_stop_flags.discard(run_id)
            current = db.get_custom_run(run_id)
            if not current or current.get("status") != "stopped":
                db.update_custom_run(run_id, status="done",
                                     ended_at=datetime.now().isoformat())

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"run_id": run_id, "total_tests": len(pw_tests), "tests": pw_tests})


@app.route("/api/custom/stream/<int:run_id>")
def api_custom_stream(run_id):
    def generate():
        eq = _custom_queues.get(run_id)
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


@app.route("/api/custom/stop/<int:run_id>", methods=["POST"])
def api_custom_stop(run_id):
    _custom_stop_flags.add(run_id)
    db.update_custom_run(run_id, status="stopped",
                         ended_at=datetime.now().isoformat())
    eq = _custom_queues.get(run_id)
    if eq:
        try:
            eq.put({"type": "pw_stopping", "run_id": run_id})
        except Exception:
            pass
    return jsonify({"ok": True})


@app.route("/api/custom/runs", methods=["GET"])
def api_custom_list_runs():
    uc_id = request.args.get("use_case_id", type=int)
    runs = db.list_custom_runs(uc_id)
    for r in runs:
        r["results"] = db.get_custom_results(r["id"])
    return jsonify(runs)


@app.route("/api/custom/runs/<int:run_id>", methods=["GET"])
def api_custom_get_run(run_id):
    run = db.get_custom_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    run["results"] = db.get_custom_results(run_id)
    return jsonify(run)


@app.route("/api/custom/runs/<int:run_id>", methods=["DELETE"])
def api_custom_delete_run(run_id):
    db.delete_custom_run(run_id)
    return jsonify({"ok": True})


@app.route("/api/custom/results/<int:result_id>/verdict", methods=["PATCH"])
def api_custom_result_verdict(result_id):
    """User manually marks a custom test result as PASS or FAIL."""
    data = request.get_json() or {}
    verdict = (data.get("verdict") or "").upper()
    if verdict not in ("PASS", "FAIL"):
        return jsonify({"error": "verdict must be PASS or FAIL"}), 400
    db.update_custom_result(result_id, overall=verdict)
    with db.db() as conn:
        row = conn.execute("SELECT run_id FROM custom_results WHERE id = ?", (result_id,)).fetchone()
        if row:
            rid = row["run_id"]
            counts = conn.execute(
                "SELECT "
                "SUM(CASE WHEN overall='PASS' THEN 1 ELSE 0 END) as p, "
                "SUM(CASE WHEN overall='FAIL' THEN 1 ELSE 0 END) as f "
                "FROM custom_results WHERE run_id = ?", (rid,)
            ).fetchone()
            conn.execute("UPDATE custom_runs SET passed = ?, failed = ? WHERE id = ?",
                         (counts["p"] or 0, counts["f"] or 0, rid))
    return jsonify({"ok": True})


@app.route("/api/custom/save-result", methods=["POST"])
def api_custom_save_result():
    """Save a result captured from a custom test run."""
    data = request.get_json() or {}
    run_id = data.get("run_id")
    if not run_id:
        return jsonify({"error": "run_id required"}), 400
    result_id = db.save_custom_result(
        run_id=run_id,
        use_case_id=data.get("use_case_id", 0),
        use_case_name=data.get("use_case_name", ""),
        test_id=data.get("test_id", ""),
        name=data.get("name", ""),
        turns=data.get("turns", []),
        overall=data.get("overall", "pending"),
        summary=data.get("summary", ""),
    )
    return jsonify({"id": result_id})


# ── Reporting ────────────────────────────────────────────────────────────────

@app.route("/reporting")
def reporting_page():
    return render_template("reporting.html")


@app.route("/api/reporting/runs", methods=["GET"])
def api_reporting_runs():
    """List all runs (Live Validate + Custom) for reporting."""
    pw_runs = db.list_playwright_runs(limit=50)
    for r in pw_runs:
        r["source"] = "live_validate"
        r["results"] = db.get_playwright_results(r["id"])
        uc_ids = r.get("use_case_ids", [])
        names = []
        for uid in uc_ids:
            uc = db.get_use_case(uid)
            if uc:
                names.append(uc["name"])
        r["agent_names"] = names

    custom_runs = db.list_custom_runs(limit=50)
    for r in custom_runs:
        r["source"] = "custom"
        r["results"] = db.get_custom_results(r["id"])
        uc = db.get_use_case(r.get("use_case_id", 0))
        r["agent_names"] = [uc["name"]] if uc else []

    all_runs = pw_runs + custom_runs
    all_runs.sort(key=lambda x: x.get("started_at", ""), reverse=True)
    return jsonify(all_runs)


@app.route("/api/reporting/download", methods=["POST"])
def api_reporting_download():
    """Download a PDF report for selected runs (single or consolidated)."""
    data = request.get_json() or {}
    run_selections = data.get("runs", [])
    if not run_selections:
        return jsonify({"error": "No runs selected."}), 400

    try:
        pdf_bytes = report_pdf.generate_consolidated_report(run_selections)
    except Exception as e:
        return jsonify({"error": f"Report generation failed: {e}"}), 500

    filename = "consolidated_report.pdf" if len(run_selections) > 1 else "test_report.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
