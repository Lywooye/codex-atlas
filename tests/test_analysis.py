import copy
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from atlas.analysis import AnalysisCancelled, AnalysisService, CodexRunner, analysis_payload, validate_result
from atlas.metadata import load_metadata


def sample_job():
    return {"id": "job-1", "thread_id": "thread-1", "thread_title": "学习与代码两个主题",
            "source_hash": "hash", "cwd": "/do-not-classify-this-directory",
            "messages": [{"id": "m1", "role": "user", "text": "整理课程，然后检查测试。", "kind": "message"},
                         {"id": "m2", "role": "assistant", "text": "课程的两个示例测试通过。", "kind": "message"}],
            "tool_evidence": [{"id": "e1", "role": "tool", "kind": "tool_output",
                               "text": "2 tests passed", "truncated": False}]}


def sample_result():
    return {"segments": [{"message_ids": ["m1", "m2"], "topic_names": ["课程工具"],
             "project": {"id": "", "name": "课程学习系统", "goal": "建设课程和配套学习工具", "parent_id": ""},
             "summary": "整理课程示例，历史记录显示两项测试通过。", "stage": "待试读",
             "events": [{"kind": "verified", "text": "两项测试通过", "message_ids": ["e1"]}],
             "open_items": ["实际试读"], "decisions": []}]}


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    script = tmp_path / "codex"
    script.write_text("#!" + sys.executable + "\n" + r'''
import json, os, pathlib, sys, time
args = sys.argv[1:]
mode = os.environ.get("FAKE_MODE", "success")
if args[0] == "app-server":
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message["method"]
        if method == "initialize":
            result = {}
        else:
            assert method == "thread/list"
            params = message["params"]
            assert params["useStateDbOnly"] is True
            assert params["sourceKinds"] and params["modelProviders"] == []
            if mode == "metadata_timeout":
                time.sleep(5)
            if params["archived"]:
                if mode == "metadata_partial":
                    print(json.dumps({"id":message["id"], "error":{"message":"archive unavailable"}}), flush=True)
                    continue
                result = {"data":[{"id":"archived", "name":"归档标题", "updatedAt":3}], "nextCursor":None}
            elif params["cursor"] is None:
                result = {"data":[{"id":"one", "name":"官方标题", "updatedAt":2}], "nextCursor":"page2"}
            else:
                result = {"data":[{"id":"two", "name":None, "updatedAt":1}],
                          "nextCursor":"page2" if mode == "metadata_loop" else None}
        print(json.dumps({"jsonrpc":"2.0", "id":message["id"], "result":result}), flush=True)
    sys.exit(0)
assert args[0] == "exec"
prompt = sys.stdin.read()
assert "do-not-classify-this-directory" not in prompt
assert "--ephemeral" in args and "--ignore-user-config" in args
assert args[args.index("--sandbox")+1] == "read-only"
assert 'model=' + json.dumps(os.environ.get('EXPECTED_MODEL', 'configured-model')) in args
assert 'model_reasoning_effort=' + json.dumps(os.environ.get('EXPECTED_EFFORT', 'high')) in args
assert "OPENAI_API_KEY" not in os.environ and "CODEX_API_KEY" not in os.environ
assert "shell_tool" in args and "plugins" in args
if mode == "sleep":
    time.sleep(5)
if mode == "error":
    print("fake login unavailable", file=sys.stderr)
    sys.exit(3)
result = json.loads(os.environ["FAKE_RESULT"])
if mode == "fallback":
    print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":json.dumps(result)}}))
else:
    pathlib.Path(args[args.index("-o")+1]).write_text(json.dumps(result))
''', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("ATLAS_CODEX_BIN", str(script))
    monkeypatch.setenv("FAKE_RESULT", json.dumps(sample_result()))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-be-inherited")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model="configured-model"\nmodel_reasoning_effort="high"\n')
    return tmp_path, codex_home


def test_validation_requires_complete_and_known_provenance():
    result = sample_result()
    assert validate_result(result, sample_job(), {"projects": []}) == result
    missing = copy.deepcopy(result)
    missing["segments"][0]["message_ids"] = ["m2"]
    with pytest.raises(ValueError, match="遗漏"):
        validate_result(missing, sample_job())
    foreign = copy.deepcopy(result)
    foreign["segments"][0]["events"][0]["message_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="证据以外"):
        validate_result(foreign, sample_job())
    foreign["segments"][0]["message_ids"].append("foreign")
    with pytest.raises(ValueError, match="不属于"):
        validate_result(foreign, sample_job())


def test_local_effort_override_preserves_codex_config(fake_codex):
    root, codex_home = fake_codex
    config = codex_home / 'config.toml'
    original = 'model="configured-model"\nmodel_reasoning_effort="ultra"\n'
    config.write_text(original)
    (root / 'data').mkdir(exist_ok=True)
    settings = root / 'data' / 'analysis-settings.json'
    settings.write_text('{"reasoning_effort":"high"}')
    runner = CodexRunner(root, codex_home)
    assert runner.analyze(sample_job(), {'projects': []}, threading.Event()) == sample_result()
    assert config.read_text() == original
    assert 'model_reasoning_effort="ultra"' not in runner.options()
    settings.write_text('{"reasoning_effort":"unsupported"}')
    with pytest.raises(RuntimeError, match='分析设置无效'):
        runner.options()


def test_local_model_override_is_used_without_changing_global_config(fake_codex, monkeypatch):
    root, codex_home = fake_codex
    original = (codex_home / 'config.toml').read_text()
    (root / 'data').mkdir()
    settings = root / 'data' / 'analysis-settings.json'
    settings.write_text('{"model":"gpt-6-luna","reasoning_effort":"medium"}')
    monkeypatch.setenv('EXPECTED_MODEL', 'gpt-6-luna')
    monkeypatch.setenv('EXPECTED_EFFORT', 'medium')
    runner = CodexRunner(root, codex_home)
    assert runner.analyze(sample_job(), {'projects': []}, threading.Event()) == sample_result()
    assert 'model="configured-model"' not in runner.options()
    assert (codex_home / 'config.toml').read_text() == original
    settings.write_text('{"reasoning_effort":"high"}')
    assert 'model="configured-model"' in runner.options()
    for model in ('', 42):
        settings.write_text(json.dumps({'model': model, 'reasoning_effort': 'medium'}))
        with pytest.raises(RuntimeError, match='分析设置无效'):
            runner.options()


def test_validation_rejects_unknown_project_and_extra_fields():
    result = sample_result()
    result["segments"][0]["project"]["id"] = "invented"
    with pytest.raises(ValueError, match="未知项目"):
        validate_result(result, sample_job(), {"projects": []})
    result["segments"][0]["project"]["id"] = "known"
    validate_result(result, sample_job(), {"projects": [{"id": "known"}]})
    result["hidden_command"] = "run this"
    with pytest.raises(ValueError, match="结构"):
        validate_result(result, sample_job())


def grouping_catalog():
    return {"projects": [
        {"id": "root", "name": "课程设计与学习工作台", "parent_id": None,
         "goal": "课程生成、核验、四门课程和 Obsidian 学习工作台服务同一学习系统",
         "children": [{"id": "course", "name": "课程内容与表达修订"}]},
        {"id": "course", "name": "课程内容与表达修订", "parent_id": "root",
         "goal": "持续修订四门课程内容、术语、表达与教学例子"},
        {"id": "other", "name": "独立教学研究", "parent_id": ""},
        {"id": "merged", "name": "旧项目", "parent_id": None, "merged_into": "root"},
    ]}


def test_existing_root_and_child_preserve_user_grouping():
    catalog = grouping_catalog()
    assert analysis_payload(sample_job(), catalog)["catalog"] == catalog
    result = sample_result()
    project = result["segments"][0]["project"]
    project.update(id="root", parent_id="")
    validate_result(result, sample_job(), catalog)
    project.update(id="course", parent_id="root")
    validate_result(result, sample_job(), catalog)
    for replacement in ("", "other"):
        project["parent_id"] = replacement
        with pytest.raises(ValueError, match="保留.*parent_id"):
            validate_result(result, sample_job(), catalog)
    project.update(id="root", parent_id="other")
    with pytest.raises(ValueError, match="保留.*parent_id"):
        validate_result(result, sample_job(), catalog)


def test_new_child_can_only_reference_existing_unmerged_root():
    result = sample_result()
    project = result["segments"][0]["project"]
    project["parent_id"] = "root"
    validate_result(result, sample_job(), grouping_catalog())
    for parent_id in ("invented", "course", "merged"):
        project["parent_id"] = parent_id
        with pytest.raises(ValueError, match="父项目必须"):
            validate_result(result, sample_job(), grouping_catalog())
    project["parent_id"] = "root"
    with pytest.raises(ValueError, match="父项目必须"):
        validate_result(result, sample_job())


def test_same_result_cannot_establish_a_parent_for_new_child():
    result = sample_result()
    child = copy.deepcopy(result["segments"][0])
    child["project"]["parent_id"] = result["segments"][0]["project"]["name"]
    result["segments"].append(child)
    with pytest.raises(ValueError, match="父项目必须"):
        validate_result(result, sample_job(), {"projects": []})


def test_merged_or_invalid_existing_hierarchy_is_rejected():
    result = sample_result()
    project = result["segments"][0]["project"]
    project["id"] = "merged"
    with pytest.raises(ValueError, match="已合并"):
        validate_result(result, sample_job(), grouping_catalog())
    for parent_id in ("root", "missing", "course", "merged"):
        catalog = grouping_catalog()
        catalog["projects"][0]["parent_id"] = parent_id
        project.update(id="root", parent_id=parent_id)
        with pytest.raises(ValueError, match="父项目必须"):
            validate_result(result, sample_job(), catalog)


@pytest.mark.parametrize("parent_id", [None, 1])
def test_parent_id_is_required_string(parent_id):
    result = sample_result()
    result["segments"][0]["project"]["parent_id"] = parent_id
    with pytest.raises(ValueError, match="应为文本"):
        validate_result(result, sample_job())
    del result["segments"][0]["project"]["parent_id"]
    with pytest.raises(ValueError, match="结构"):
        validate_result(result, sample_job())


def test_topic_only_segment_cannot_reference_parent():
    result = sample_result()
    result["segments"][0]["project"] = {"id": "", "name": "", "goal": "", "parent_id": "root"}
    with pytest.raises(ValueError, match="名称与目标不完整"):
        validate_result(result, sample_job(), grouping_catalog())


def test_background_context_is_not_required_coverage_or_citable_evidence():
    job = sample_job()
    job["messages"] = [job["messages"][1]]
    job["context"] = [{"id": "earlier-user", "role": "user", "kind": "message",
                       "text": "验证课程的两个示例", "truncated": True}]
    payload = analysis_payload(job, {})
    assert payload["context"][0]["truncated"] is True
    assert "cwd" not in payload
    result = sample_result()
    result["segments"][0]["message_ids"] = ["m2"]
    validate_result(result, job, {"projects": []})
    result["segments"][0]["message_ids"].append("earlier-user")
    with pytest.raises(ValueError, match="不属于"):
        validate_result(result, job)


def test_tool_only_chunk_without_user_goal_cannot_create_project():
    job = sample_job()
    job["messages"] = [job["messages"][1]]
    job["messages"][0]["role"] = "tool"
    job["messages"][0]["kind"] = "tool_output"
    result = sample_result()
    result["segments"][0]["message_ids"] = ["m2"]
    with pytest.raises(ValueError, match="孤立工具"):
        validate_result(result, job)
    result["segments"][0]["project"] = {"id": "", "name": "", "goal": "", "parent_id": ""}
    validate_result(result, job)


@pytest.mark.parametrize("background", [
    "not a list",
    [{"id": "m1", "role": "user", "text": "duplicate source"}],
    [{"id": "context", "role": "system", "text": "instructions"}],
    [{"id": "context", "role": "user", "text": "other thread", "thread_id": "other"}],
])
def test_context_structure_and_same_thread_are_validated(background):
    job = sample_job()
    job["context"] = background
    with pytest.raises(ValueError):
        analysis_payload(job, {})


def test_sampled_tool_evidence_has_refs_but_is_not_segment_coverage():
    job = sample_job()
    job["tool_evidence"].append({"id": "e2", "role": "tool", "kind": "tool_output",
                                 "text": "partial log", "truncated": True,
                                 "source_ranges": [[0, 11]]})
    job["evidence_sampling"] = {"total_outputs": 4, "sampled_outputs": 2,
                                 "omitted_outputs": 2, "truncated_outputs": 1}
    payload = analysis_payload(job, {})
    assert payload["evidence_sampling"]["omitted_outputs"] == 2
    assert payload["tool_evidence"][1]["truncated"] is True
    result = sample_result()
    validate_result(result, job)
    result["segments"][0]["events"][0]["message_ids"] = ["omitted-evidence"]
    with pytest.raises(ValueError, match="证据以外"):
        validate_result(result, job)
    result = sample_result()
    result["segments"][0]["message_ids"].append("e1")
    with pytest.raises(ValueError, match="不属于"):
        validate_result(result, job)


def test_duplicate_or_foreign_tool_evidence_is_rejected():
    job = sample_job()
    job["tool_evidence"][0]["id"] = "m1"
    with pytest.raises(ValueError, match="重复"):
        analysis_payload(job, {})
    job = sample_job()
    job["tool_evidence"][0]["thread_id"] = "elsewhere"
    with pytest.raises(ValueError, match="其他会话"):
        analysis_payload(job, {})


@pytest.mark.parametrize("mode", ["success", "fallback"])
def test_runner_uses_ephemeral_read_only_default_model(fake_codex, monkeypatch, mode):
    root, codex_home = fake_codex
    monkeypatch.setenv("FAKE_MODE", mode)
    runner = CodexRunner(root, codex_home, timeout=3)
    assert runner.analyze(sample_job(), {"projects": [], "topics": []}, threading.Event()) == sample_result()
    assert not list((root / "data" / "analysis").iterdir())


def test_runner_reports_exit_timeout_and_cancellation(fake_codex, monkeypatch):
    root, codex_home = fake_codex
    runner = CodexRunner(root, codex_home, timeout=3)
    monkeypatch.setenv("FAKE_MODE", "error")
    with pytest.raises(RuntimeError, match="fake login unavailable"):
        runner.analyze(sample_job(), {}, threading.Event())
    monkeypatch.setenv("FAKE_MODE", "sleep")
    runner.timeout = .3
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        runner.analyze(sample_job(), {}, threading.Event())
    assert time.monotonic() - started < 3
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AnalysisCancelled):
        runner.analyze(sample_job(), {}, cancel)


def test_custom_provider_is_not_silently_changed(fake_codex):
    root, codex_home = fake_codex
    (codex_home / "config.toml").write_text('model="configured-model"\nmodel_provider="private-server"\n')
    with pytest.raises(RuntimeError, match="自定义 provider"):
        CodexRunner(root, codex_home).analyze(sample_job(), {}, threading.Event())


class FakeStore:
    def __init__(self):
        self.pending = [sample_job()]
        self.applied = []
        self.failed = []
        self.claimed = []
        self.recovered = 0
        self.settings = {"approved": True, "paused": False}

    def recover_jobs(self):
        self.recovered += 1

    def claim_job(self):
        if self.pending:
            job = self.pending[0]
            if job.get('next_retry_at', 0) > time.time():
                return None
            self.pending.pop(0)
            job['attempts'] = job.get('attempts', 0)+1
            self.claimed.append(job)
            return job

    def queue_settings(self):
        return self.settings.copy()

    def save_queue_settings(self, values):
        self.settings = values.copy()

    def schedule_retry(self, job_id, error, delay):
        job = next(j for j in reversed(self.claimed) if j['id'] == job_id)
        job['next_retry_at'] = time.time()+delay
        self.pending.append(job)
        return job['next_retry_at']

    def catalog(self):
        return {"projects": [], "topics": []}

    def apply_analysis(self, job_id, result):
        self.applied.append(job_id)

    def fail_job(self, job_id, error):
        self.failed.append((job_id, error))

    def retry_failed(self):
        count = len(self.failed)
        self.pending.extend(sample_job() for _ in self.failed)
        self.failed.clear()
        return count


class GateRunner:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail = False

    def analyze(self, job, catalog, cancel):
        self.started.set()
        while not self.release.wait(.01):
            if cancel.is_set():
                raise AnalysisCancelled()
        if self.fail:
            raise RuntimeError("quota unavailable")
        return sample_result()


def eventually(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("condition not reached")
        time.sleep(.02)


def test_worker_lease_pause_running_job_and_shutdown(tmp_path):
    store = FakeStore()
    service = AnalysisService(store, tmp_path, tmp_path)
    runner = GateRunner()
    service.runner = runner
    service.start()
    try:
        assert not runner.started.wait(.15)
        service.control("pause")
        service.heartbeat("browser")
        assert not runner.started.wait(.15)
        service.control("resume")
        assert runner.started.wait(2)
        assert service.status()["active"] is True
        store.pending.append({**sample_job(), "id": "second"})
        service.control("pause")
        runner.release.set()
        eventually(lambda: len(store.applied) == 1)
        assert len(store.claimed) == 1
        with service._lock:
            service._clients["browser"] = time.monotonic() - 76
        service.control("resume")
        time.sleep(.15)
        assert service.status()["clients"] == 0
        assert len(store.claimed) == 1
        service.heartbeat("browser")
        eventually(lambda: len(store.applied) == 2)
    finally:
        service.stop()
    assert not service._thread.is_alive()


def test_worker_service_failure_recovers_automatically(tmp_path):
    store = FakeStore()
    service = AnalysisService(store, tmp_path, tmp_path)
    runner = GateRunner()
    runner.fail = True
    service.RETRY_DELAYS = (.02, .04)
    service.SERVICE_RETRY_BASE = .02
    runner.release.set()
    service.runner = runner
    service.start()
    try:
        service.heartbeat("browser")
        eventually(lambda: service.status()["error"])
        assert "quota unavailable" in service.status()["error"]
        assert not service.status()["paused"]
        assert not store.failed
        runner.fail = False
        eventually(lambda: store.applied)
        assert service.status()["last_success"]
        assert not store.failed
    finally:
        service.stop()


def test_worker_stop_leaves_claim_for_restart_recovery(tmp_path):
    store = FakeStore()
    service = AnalysisService(store, tmp_path, tmp_path)
    runner = GateRunner()
    service.runner = runner
    service.start()
    service.heartbeat("browser")
    assert runner.started.wait(2)
    service.stop()
    assert not service._thread.is_alive()
    assert not store.applied and not store.failed
    assert store.recovered == 1


def test_replaced_tail_job_is_discarded_without_pausing_queue(tmp_path):
    class ReplacingStore(FakeStore):
        def apply_analysis(self, job_id, result):
            if job_id == "job-1":
                raise KeyError("old tail replaced during synchronization")
            super().apply_analysis(job_id, result)

    store = ReplacingStore()
    store.pending.append({**sample_job(), "id": "replacement"})
    service = AnalysisService(store, tmp_path, tmp_path)
    runner = GateRunner()
    runner.release.set()
    service.runner = runner
    service.start()
    try:
        service.heartbeat("browser")
        eventually(lambda: store.applied == ["replacement"])
        assert not store.failed
        assert not service.status()["paused"]
        assert service.status()["error"] is None
    finally:
        service.stop()


def test_metadata_paginates_active_and_archive(fake_codex):
    _, codex_home = fake_codex
    mapping, error = load_metadata(codex_home, timeout=3)
    assert error is None
    assert set(mapping) == {"one", "two", "archived"}
    assert mapping["one"]["title"] == "官方标题"
    assert mapping["two"]["title"] == ""
    assert mapping["archived"]["archived"] is True


@pytest.mark.parametrize("mode,expected", [("metadata_partial", "archive unavailable"),
                                            ("metadata_loop", "游标")])
def test_metadata_preserves_partial_data_with_explicit_error(fake_codex, monkeypatch, mode, expected):
    _, codex_home = fake_codex
    monkeypatch.setenv("FAKE_MODE", mode)
    mapping, error = load_metadata(codex_home, timeout=3)
    assert "one" in mapping and expected in error


def test_metadata_has_total_deadline(fake_codex, monkeypatch):
    _, codex_home = fake_codex
    monkeypatch.setenv("FAKE_MODE", "metadata_timeout")
    started = time.monotonic()
    mapping, error = load_metadata(codex_home, timeout=.3)
    assert mapping == {} and "超时" in error
    assert time.monotonic() - started < 3


def test_bad_batch_exhausts_retries_without_blocking_others(tmp_path):
    store = FakeStore()
    store.pending.append({**sample_job(), 'id': 'good'})
    class Runner:
        def analyze(self, job, catalog, cancel):
            if job['id'] == 'job-1':
                raise ValueError('invalid citation')
            return sample_result()
    service = AnalysisService(store, tmp_path, tmp_path)
    service.runner = Runner()
    service.RETRY_DELAYS = (.01, .02)
    service.SERVICE_RETRY_BASE = .01
    service.start()
    try:
        service.heartbeat('browser')
        eventually(lambda: len(store.failed) == 1 and store.applied == ['good'], seconds=5)
        assert len([j for j in store.claimed if j['id'] == 'job-1']) == 3
        assert not service.status()['paused']
    finally:
        service.stop()


def test_background_pause_and_cooldown_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('ATLAS_BACKGROUND', '1')
    store = FakeStore()
    first = AnalysisService(store, tmp_path, tmp_path)
    first.control('pause')
    first._cooldown_until = time.time()+100
    first._failures = 4
    first._save_control()
    service = AnalysisService(store, tmp_path, tmp_path)
    runner = GateRunner()
    runner.release.set()
    service.runner = runner
    service.start()
    try:
        assert service.status()['background'] and service.status()['paused']
        assert not runner.started.wait(.1)
        service.control('resume')
        assert not runner.started.wait(.1)  # An outage cooldown is still respected.
        service._cooldown_until = 0
        service._wake.set()
        eventually(lambda: store.applied)
        assert service.status()['clients'] == 0
    finally:
        service.stop()


def test_worker_recovers_from_queue_exception(tmp_path):
    class FlakyStore(FakeStore):
        broken = True
        def claim_job(self):
            if self.broken:
                self.broken = False
                raise OSError('temporary database problem')
            return super().claim_job()
    store = FlakyStore()
    service = AnalysisService(store, tmp_path, tmp_path)
    service.INTERNAL_RETRY_DELAY = .01
    runner = GateRunner()
    runner.release.set()
    service.runner = runner
    service.start()
    try:
        service.heartbeat('browser')
        eventually(lambda: store.applied)
        assert service._thread.is_alive() and not service.status()['paused']
    finally:
        service.stop()
