#!/usr/bin/env python3
"""Isolated, bound diagnostic/listening/mock runtime. Not a release entrypoint."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import run_n5_g3_integrated_acceptance as g3
import run_n5_entry_diagnostic_app as diagnostic
from n5_diagnostic_presentation import present as present_diagnostic
from run_n5_g3_reading_app import SESSION_ID_PATTERN, read_json, utc_now, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "product/n5/course/learning-route-runtime-v1/runtime"
CONTRACT = "n5-learning-route-candidate-v1"
UNITS = {unit["unit_code"]: unit for unit in g3.LISTENING_PUBLIC["units"]}
KINDS = {"g3_listening", "g3_mock", "entry_diagnostic"}


class RouteSession:
    def __init__(self, record, session_id, kind, unit, binding, apply_diagnostic=None):
        if not SESSION_ID_PATTERN.fullmatch(session_id) or kind not in KINDS:
            raise ValueError("invalid route session identity")
        if kind == "g3_listening" and unit not in UNITS:
            raise ValueError("unknown listening unit")
        self.record, self.apply_diagnostic = Path(record), apply_diagnostic
        self.lock = threading.RLock()
        self.identity = dict(contract=CONTRACT, session_id=session_id, kind=kind, unit=unit, binding=binding)
        if self.record.exists():
            self.data = read_json(self.record)
            if self.data.get("identity") != self.identity:
                raise ValueError("stored activity identity mismatch")
        else:
            self.data = dict(identity=self.identity, token=secrets.token_urlsafe(24), stage={
                "g3_listening": "teaching", "g3_mock": "device", "entry_diagnostic": "intake",
            }[kind], index=0, selected=None, responses=[], audio=None, device_confirmed=False,
                created_at=utc_now(), completed_at=None, technical_events=[],
                mock_section_index=0, mock_answers={}, mock_submitted_sections=[],
                mock_deadline_epoch=None, mock_audio_plays={}, intake={}, diagnostic_responses=[],
                diagnostic_result=None, fragment_order=[], handoff=None)
            self.save()

    def save(self):
        write_json_atomic(self.record, self.data)

    def items(self, pool=None):
        return [i for i in UNITS[self.identity["unit"]]["items"] if i["pool"] == (pool or self.data["stage"])]

    def current(self):
        d = self.data
        if self.identity["kind"] == "g3_listening":
            pool = "practice" if d["stage"] in {"feedback", "pending_feedback"} else "stage_check" if d["stage"] == "pending_check" else d["stage"]
            return self.items(pool)[d["index"]]
        if self.identity["kind"] == "g3_mock":
            return g3.MOCK_PUBLIC["sections"][d["mock_section_index"]]["items"][d["index"]]
        return d["diagnostic_result"]["item"]

    def expire(self):
        d = self.data
        if self.identity["kind"] == "g3_mock" and d["stage"] == "mock_section" and time.time() >= d["mock_deadline_epoch"]:
            self.finish_section()
            d["token"] = secrets.token_urlsafe(24)
            self.save()

    def finish_section(self):
        d = self.data
        d["mock_submitted_sections"].append(g3.MOCK_PUBLIC["sections"][d["mock_section_index"]]["section_id"])
        d["mock_deadline_epoch"] = None
        d["audio"] = None
        if d["mock_section_index"] == 2:
            self.complete()
        else:
            d["stage"] = "between_sections"
        d["index"] = 0

    def complete(self):
        self.data["stage"] = "completed"
        self.data["completed_at"] = utc_now()

    def safe(self):
        self.expire()
        d, kind = self.data, self.identity["kind"]
        out = dict(contract=CONTRACT, session_id=self.identity["session_id"], kind=kind,
                   unit=self.identity["unit"], stage=d["stage"], action_token=d["token"], index=d["index"],
                   selected=d["selected"], device_confirmed=d["device_confirmed"],
                   audio={k:v for k,v in (d["audio"] or {}).items() if k != "path"},
                   status="completed" if d["stage"] == "completed" else "in_progress")
        if kind == "g3_listening":
            out["title"] = UNITS[self.identity["unit"]]["title_zh"]
            if d["stage"] != "completed":
                item = self.current()
                out["item"] = {k:v for k,v in item.items() if k in {"item_id", "prompt_zh", "options", "pool"}}
                out["count"] = len(self.items(item["pool"]))
                if d["stage"] == "teaching":
                    out["item"].update(transcript_ja=item["transcript_ja"], explanation_zh=item["explanation_zh"])
                if d["stage"] == "feedback":
                    key = g3.LISTENING_ASSESSMENT[item["item_id"]]
                    out["feedback"] = dict(correct_option_id=key["correct_option_id"], explanation_zh=key["explanation_zh"], selected=d["selected"])
        elif kind == "g3_mock":
            out["section_index"] = d["mock_section_index"]
            out["deadline_epoch"] = d["mock_deadline_epoch"]
            if d["stage"] == "mock_section":
                section = g3.MOCK_PUBLIC["sections"][d["mock_section_index"]]
                item = self.current()
                out.update(title=section["title_zh"], count=len(section["items"]), item=g3.clean_public_item(item, "mock"), selected=d["mock_answers"].get(item["item_id"]))
                out["item"].pop("audio_path", None)
                out["has_audio"] = "audio_segments" in g3.MOCK_ASSESSMENT[item["item_id"]]
                out["audio_used"] = d["mock_audio_plays"].get(item["item_id"], 0)
            elif d["stage"] == "between_sections":
                out["next_title"] = g3.MOCK_PUBLIC["sections"][d["mock_section_index"]+1]["title_zh"]
        else:
            out.update(diagnostic=d["diagnostic_result"], intake=d["intake"], fragment_order=d["fragment_order"], handoff=d["handoff"])
            out['diagnostic_presentation'] = present_diagnostic(diagnostic, d['diagnostic_responses'], d['stage'])
        if d["stage"] == "completed" and kind != "entry_diagnostic":
            out["result"] = self.result()
        return out

    def result(self):
        d, kind = self.data, self.identity["kind"]
        if d["stage"] != "completed" or kind == "entry_diagnostic":
            return dict(status="pending", session_id=self.identity["session_id"])
        if kind == "g3_mock":
            sections = g3.section_results(SimpleNamespace(data=d))
            answered, correct = sum(x["attempted"] for x in sections), sum(x["correct"] for x in sections)
            reviews = g3.review_priorities(SimpleNamespace(data=d))
            work_unit = "n5-self-authored-comprehensive-mock-001-v1"
        else:
            answered = len(d["responses"])
            correct = sum(x["correct"] for x in d["responses"])
            reviews = sorted({g3.LISTENING_ITEMS[x["item_id"]]["primary_l2_object_id"] for x in d["responses"] if not x["correct"]})
            work_unit = f"n5-listening-{self.identity['unit'].lower()}-v1"
        return dict(schema_version=1, result_id="result:"+self.identity["session_id"], session_id=self.identity["session_id"],
                    work_unit_id=work_unit, status="completed", completed_at=d["completed_at"],
                    evidence_summary=dict(answered_count=answered, correct_count=correct, session_mode="assessment",
                        scoring_contract_id=CONTRACT, mastery_claim="not_inferred", review_priority_l2_object_ids=reviews),
                    official_scaled_score=None, jlpt_pass_decision=None, mastery_decision=None)

    def act(self, body):
        with self.lock:
            self.expire()
            if body.get("action_token") != self.data["token"]:
                raise ValueError("页面状态已更新，请重新连接后继续")
            before = copy.deepcopy(self.data)
            try:
                self._act(body)
                self.data["token"] = secrets.token_urlsafe(24)
                self.save()
            except Exception:
                self.data = before
                raise
            return self.safe()

    def _act(self, b):
        d, kind, action = self.data, self.identity["kind"], b.get("action")
        stage = d["stage"]
        if action == "save_exit":
            return
        if action == "play":
            if d["audio"] and d["audio"]["status"] == "playing":
                raise ValueError("当前音频尚未结束")
            if stage == "device":
                path = g3.MOCK / "audio/n5-g3-device-check-v1.mp3"
                audio_id = "device"
            elif kind == "entry_diagnostic" and stage == "question":
                item = self.current()
                asset = b.get("asset")
                allowed = [item["stimulus"].get("audio_url")] + [o.get("audio_url") for o in item["options"]]
                if asset not in allowed or not asset:
                    raise ValueError("当前题目不能播放这段音频")
                audio_id = item["diagnostic_item_id"] + asset
                if d["mock_audio_plays"].get(audio_id, 0) >= item["maximum_plays_per_clip"]:
                    raise ValueError("本段音频已达到播放次数上限")
                path = diagnostic.ASSET_PATHS[asset.removeprefix("/api/assets/")]
                d["mock_audio_plays"][audio_id] = d["mock_audio_plays"].get(audio_id, 0)+1
            elif (kind == "g3_listening" and stage in {"teaching", "practice", "feedback", "stage_check", "pending_feedback"}) or (kind == "g3_mock" and stage == "mock_section"):
                item = self.current()
                audio_id = item["item_id"]
                path = (g3.LISTENING if kind == "g3_listening" else g3.MOCK) / "audio" / (audio_id+".mp3")
                if kind == "g3_mock":
                    if "audio_segments" not in g3.MOCK_ASSESSMENT[audio_id] or d["mock_audio_plays"].get(audio_id, 0):
                        raise ValueError("模考听力每题只播放一次")
                    d["mock_audio_plays"][audio_id] = 1
            else:
                raise ValueError("当前阶段不能播放")
            if not path.is_file():
                raise ValueError("音频文件缺失")
            d["audio"] = dict(id=audio_id, path=str(path), status="playing", position=0, ticket=secrets.token_urlsafe(20))
            return
        if action in {"audio_position", "audio_ended", "audio_failed", "resume_audio"}:
            a = d["audio"]
            if not a or b.get("ticket") != a["ticket"] or a["status"] not in {"playing", "interrupted"}:
                raise ValueError("音频状态不匹配")
            if action == "audio_position":
                position = b.get("position")
                if isinstance(position, bool) or not isinstance(position, (int, float)) or not 0 <= position <= 3600:
                    raise ValueError("无效的播放位置")
                a["position"] = position
            elif action == "resume_audio":
                a["status"] = "playing"
            elif action == "audio_ended":
                a["status"] = "ended"
                if a["id"] == "device":
                    d["device_confirmed"] = True
                if stage == "pending_feedback":
                    d["stage"] = "feedback"
                elif stage == "pending_check":
                    self.complete()
            else:
                a["status"] = "interrupted"
                d["technical_events"].append(dict(kind="audio_interrupted", audio_id=a["id"]))
            return
        if kind == "g3_listening":
            if stage == "teaching" and action in {"next", "skip_teaching"}:
                if action == "next" and d["index"]+1 < len(self.items()):
                    d["index"] += 1
                else:
                    d.update(stage="practice", index=0)
                d.update(audio=None, selected=None)
                return
            if stage in {"practice", "stage_check"} and action in {"select", "confirm"}:
                item = self.current()
                option = b.get("option_id", d["selected"])
                if option not in {o["option_id"] for o in item["options"]}:
                    raise ValueError("请选择有效答案")
                d["selected"] = option
                if action == "confirm":
                    if not d["audio"] or d["audio"]["id"] != item["item_id"]:
                        raise ValueError("请先播放本题音频")
                    d["responses"].append(dict(item_id=item["item_id"], selected=option, correct=option == g3.LISTENING_ASSESSMENT[item["item_id"]]["correct_option_id"], pool=stage))
                    if stage == "stage_check":
                        # A check result is never exposed during the audio.
                        if d["audio"]["status"] == "ended":
                            self.complete()
                        else:
                            d["stage"] = "pending_check"
                    else:
                        d["stage"] = "feedback" if d["audio"]["status"] == "ended" else "pending_feedback"
                return
            if stage == "pending_check" and action == "next" and d["audio"]["status"] == "ended":
                self.complete()
                return
            if stage == "feedback" and action == "next":
                if d["index"]+1 < len(self.items("practice")):
                    d.update(stage="practice", index=d["index"]+1)
                else:
                    d.update(stage="stage_check", index=0)
                d.update(selected=None, audio=None)
                return
        elif kind == "g3_mock":
            if action == "start_section" and (stage == "device" and d["device_confirmed"] or stage == "between_sections"):
                if stage == "between_sections":
                    d["mock_section_index"] += 1
                d.update(stage="mock_section", index=0, audio=None, mock_deadline_epoch=time.time()+g3.MOCK_PUBLIC["sections"][d["mock_section_index"]]["duration_minutes"]*60)
                return
            if stage == "mock_section":
                item = self.current()
                if action == "select":
                    if b.get("option_id") not in {o["option_id"] for o in item["options"]}:
                        raise ValueError("请选择有效答案")
                    d["mock_answers"][item["item_id"]] = b["option_id"]
                    return
                if action == "next":
                    if d["audio"] and d["audio"]["status"] != "ended":
                        raise ValueError("请等待本题音频结束")
                    if item["item_id"] not in d["mock_answers"]:
                        raise ValueError("请先选择答案")
                    if d["index"]+1 < len(g3.MOCK_PUBLIC["sections"][d["mock_section_index"]]["items"]):
                        d["index"] += 1
                        d["audio"] = None
                    else:
                        self.finish_section()
                    return
        else:
            if stage == "intake" and action == "begin_diagnostic":
                intake = b.get("intake", {})
                allowed = dict(experience={"never","days","weeks","months"}, kana={"none","some","hiragana","both"}, target={"none","30","60","90"}, weekly_time={"under3","3to5","5to8","over8"})
                if set(intake) != set(allowed) or any(intake[k] not in allowed[k] for k in allowed):
                    raise ValueError("请完整填写背景信息")
                d["intake"] = intake
                self.advance_diagnostic()
                return
            if stage == "question" and action in {"select", "confirm", "device_failure"}:
                item = self.current()
                if action == "device_failure":
                    d["ended_reason"] = "persistent_audio_playback_failure"
                    self.advance_diagnostic()
                    return
                if item["response_mode"] == "ordered_fragments":
                    order = b.get("fragment_order", d["fragment_order"])
                    valid = {x["fragment_id"] for x in item["stimulus"]["fragments"]}
                    if not isinstance(order, list) or len(set(order)) != len(order) or not set(order) <= valid:
                        raise ValueError("词块顺序无效")
                    d["fragment_order"] = order
                    response = dict(fragment_order=order)
                else:
                    option = b.get("option_id", d["selected"])
                    if option not in {o["option_id"] for o in item["options"]}:
                        raise ValueError("选项无效")
                    d["selected"] = option
                    response = dict(option_id=option)
                if action == "confirm":
                    response["diagnostic_item_id"] = item["diagnostic_item_id"]
                    diagnostic.normalize_response(diagnostic.ITEMS[item["diagnostic_item_id"]], response)
                    d["diagnostic_responses"].append(response)
                    self.advance_diagnostic()
                return
            if stage == "diagnostic_result" and action == "apply_diagnostic":
                if not self.apply_diagnostic:
                    raise ValueError("该验收会话未绑定学习档案，不能应用起点")
                if not d["handoff"]:
                    d["handoff"] = self.apply_diagnostic(self)
                return
        raise ValueError("当前阶段不接受这个操作")

    def advance_diagnostic(self):
        d = self.data
        d["diagnostic_result"] = diagnostic.advance(dict(session_id=self.identity["session_id"], responses=d["diagnostic_responses"], intake=d["intake"], ended_reason=d.get("ended_reason")))
        d.update(stage="diagnostic_result" if d["diagnostic_result"]["status"] == "complete" else "question", selected=None, fragment_order=[], audio=None)


class Handler(BaseHTTPRequestHandler):
    session: RouteSession
    instance: str

    def log_message(self, *_args):
        pass

    def send(self, value, status=200, mime="application/json; charset=utf-8", extra_headers=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        for k,v in {"Content-Type":mime,"Content-Length":str(len(raw)),"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'self'; media-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"}.items():
            self.send_header(k,v)
        for key,value in (extra_headers or {}).items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self, parsed):
        if parse_qs(parsed.query).get("session_id") != [self.session.identity["session_id"]]:
            raise ValueError("会话身份不匹配")

    def do_GET(self):
        p = urlparse(self.path)
        try:
            if p.path == "/favicon.ico":
                self.send(b"", status=204, mime="image/x-icon")
                return
            if p.path == "/health":
                self.send(dict(status="ok",instance=self.instance,**self.session.identity))
                return
            if p.path in {"/app.js","/styles.css"}:
                self.send((RUNTIME/p.path[1:]).read_bytes(),mime="text/javascript" if p.path.endswith("js") else "text/css")
                return
            self.authorized(p)
            with self.session.lock:
                if p.path == "/":
                    self.send((RUNTIME/"index.html").read_bytes(),mime="text/html; charset=utf-8")
                elif p.path == "/api/state":
                    self.send(self.session.safe())
                elif p.path == "/api/result":
                    self.session.expire()
                    self.send(self.session.result())
                elif p.path == "/media":
                    a = self.session.data["audio"]
                    if not a or parse_qs(p.query).get("ticket") != [a["ticket"]]:
                        raise ValueError("无效音频请求")
                    raw=Path(a['path']).read_bytes()
                    byte_range=self.headers.get('Range')
                    if byte_range:
                        match=re.fullmatch(r'bytes=(\d*)-(\d*)',byte_range)
                        if not match or not any(match.groups()):
                            self.send(b'',416,extra_headers={'Content-Range':f'bytes */{len(raw)}'})
                            return
                        start=int(match[1]) if match[1] else max(0,len(raw)-int(match[2]))
                        end=min(int(match[2]),len(raw)-1) if match[1] and match[2] else len(raw)-1
                        if start>end:
                            self.send(b'',416,extra_headers={'Content-Range':f'bytes */{len(raw)}'})
                            return
                        self.send(raw[start:end+1],206,'audio/mpeg',{'Accept-Ranges':'bytes','Content-Range':f'bytes {start}-{end}/{len(raw)}'})
                    else:
                        self.send(raw,mime='audio/mpeg',extra_headers={'Accept-Ranges':'bytes'})
                elif p.path == "/visual" and self.session.identity["kind"] == "entry_diagnostic" and self.session.data["stage"] == "question":
                    asset = self.session.current()["stimulus"].get("visual_url")
                    if not asset:
                        raise ValueError("当前题目没有插图")
                    path = diagnostic.ASSET_PATHS[asset.removeprefix("/api/assets/")]
                    self.send(path.read_bytes(),mime=mimetypes.guess_type(path.name)[0])
                else:
                    self.send(dict(status="error",message="未找到页面"),404)
        except (ValueError, KeyError, OSError) as exc:
            self.send(dict(status="error",message=str(exc)),400)

    def do_POST(self):
        try:
            p = urlparse(self.path)
            self.authorized(p)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_address[1]}":
                raise ValueError("拒绝跨来源操作")
            if p.path != "/api/action" or self.headers.get_content_type() != "application/json":
                raise ValueError("不支持的请求")
            size = int(self.headers.get("Content-Length",0))
            if not 0 < size <= 20000:
                raise ValueError("请求大小无效")
            b = json.loads(self.rfile.read(size))
            if not isinstance(b,dict):
                raise ValueError("无效操作")
            self.send(self.session.act(b))
        except (ValueError, KeyError, OSError) as exc:
            self.send(dict(status="error",message=str(exc)),400)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host",default="127.0.0.1",choices=["127.0.0.1"])
    p.add_argument("--port",default=0,type=int)
    p.add_argument("--session-id",required=True)
    p.add_argument("--kind",required=True,choices=sorted(KINDS))
    p.add_argument("--unit-code",default="")
    p.add_argument("--binding",required=True)
    p.add_argument("--session-record",required=True,type=Path)
    p.add_argument("--ready-file",required=True,type=Path)
    p.add_argument("--bridge-script",type=Path)
    p.add_argument("--course-root",type=Path)
    p.add_argument("--archive",type=Path)
    args = p.parse_args()
    apply = None
    if args.bridge_script:
        import sys
        sys.path.insert(0,str(args.bridge_script.resolve().parent))
        import learning_bridge
        apply = lambda session: learning_bridge.apply_route_diagnostic(args, session)
    Handler.session = RouteSession(args.session_record,args.session_id,args.kind,args.unit_code,args.binding,apply)
    Handler.instance = secrets.token_urlsafe(24)
    from n5_browser_continuation import attach
    server = ThreadingHTTPServer((args.host,args.port),attach(Handler, ROOT, 'route'))
    write_json_atomic(args.ready_file,dict(base_url=f"http://127.0.0.1:{server.server_address[1]}",instance=Handler.instance,**Handler.session.identity))
    server.serve_forever()


if __name__ == "__main__":
    main()
