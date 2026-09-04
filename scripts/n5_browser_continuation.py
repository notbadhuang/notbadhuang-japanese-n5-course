"""Opt-in handler adapter for versioned local candidates, with same-origin controls."""
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, urlsplit


def reference_asset(root, relative):
    """Only serve manifest-bound public reference files, never runtime/archive paths."""
    from learning_reference import safe_file
    if not relative.startswith('reference/'):
        raise ValueError('not a public reference path')
    manifest = json.loads(safe_file(root, 'course-package.json').read_text())
    if manifest.get('learning_tour') != {'contract':'n5-first-use-tour-v1', 'entry':'reference/tour.html'}:
        raise ValueError('tour not supported')
    if manifest.get('learning_reference') != {'contract':'n5-read-only-reference-v1', 'entry':'reference/index.html'}:
        raise ValueError('reference not supported')
    index = safe_file(root, 'reference/manifest.json').read_bytes()
    if hashlib.sha256(index).hexdigest() != manifest['artifacts'].get('reference/manifest.json'):
        raise ValueError('reference manifest changed')
    expected = json.loads(index)['files'].get(relative[len('reference/'):])
    if not expected or expected != manifest['artifacts'].get(relative):
        raise ValueError('not a declared public reference asset')
    data = safe_file(root, relative).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('reference asset changed')
    return data, mimetypes.guess_type(relative)[0] or 'application/octet-stream'


def attach(base, root, family):
    archive = os.environ.get('N5_CONTINUATION_ARCHIVE')
    if not archive:
        return base
    if Path(os.environ['N5_CONTINUATION_ROOT']).resolve() != root.resolve():
        raise ValueError('continuation course root mismatch')
    sys.path.insert(0,str(Path(os.environ['N5_CONTINUATION_BRIDGE']).parent))
    from browser_continuation import Continuation
    from learning_bridge import ContractError
    control=Continuation(root,Path(archive))
    secret=secrets.token_bytes(32)
    assets=root/'product/n5/course/learning-route-runtime-v1/runtime'

    class Connected(base):
        def continuation_origin(self):
            return f'http://127.0.0.1:{self.server.server_address[1]}'

        def continuation_id(self):
            query=parse_qs(urlsplit(self.path).query)
            if len(query.get('session_id',[]))!=1:
                raise ValueError('missing session identity')
            sid=query['session_id'][0]
            control.receipt(sid,self.continuation_origin())
            return sid

        def continuation_token(self,sid):
            return hmac.new(secret,sid.encode(),hashlib.sha256).hexdigest()

        def continuation_send(self,value,status=200,mime='application/json; charset=utf-8'):
            raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status)
            for key,val in {'Content-Type':mime,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}.items():
                self.send_header(key,val)
            self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            path=urlsplit(self.path).path
            if path == '/api/learning-tour':
                try:
                    from learning_reference import resolve
                    result = resolve(root, 'tour')
                    self.continuation_send(dict(status=result['status'], url='/reference/tour.html#1' if result['status']=='ready' else None))
                except (ImportError, ValueError, KeyError, OSError, TypeError):
                    self.continuation_send(dict(status='unavailable', url=None))
                return
            if path.startswith('/reference/'):
                try:
                    data, mime = reference_asset(root, path[1:])
                    self.continuation_send(data, mime=mime)
                except (ImportError, ValueError, KeyError, OSError, TypeError):
                    self.continuation_send(dict(message='使用介绍或资料暂时无法打开，请回 WorkBuddy 检查课程文件。'),404)
                return
            if path in {'/continuity.js','/continuity.css'}:
                self.continuation_send((assets/path[1:]).read_bytes(),mime='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                return
            if path=='/api/continuity':
                try:
                    sid=self.continuation_id()
                    self.continuation_send(dict(token=self.continuation_token(sid)))
                except (ValueError,KeyError,OSError,ContractError):
                    self.continuation_send(dict(message='无法核对当前学习会话，请通过 WorkBuddy 恢复。'),400)
                return
            if path=='/favicon.ico':
                self.continuation_send(b'',204,'image/x-icon');return
            super().do_GET()

        def do_POST(self):
            if urlsplit(self.path).path!='/api/continuity':
                return super().do_POST()
            try:
                sid=self.continuation_id()
                origin=self.continuation_origin()
                if self.headers.get('Host')!=urlsplit(origin).netloc or self.headers.get('Origin') not in {None,origin}:
                    raise ValueError('cross origin')
                if self.headers.get_content_type()!='application/json' or not hmac.compare_digest(self.headers.get('X-N5-Continuation',''),self.continuation_token(sid)):
                    raise ValueError('invalid request token')
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<=2048:
                    raise ValueError('invalid request size')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict) or set(body)-{'action','plan_key','selected'}:
                    raise ValueError('invalid action body')
                if body.get('action')=='sync':
                    result=control.sync(sid,origin)
                elif body.get('action')=='continue':
                    result=control.start(sid,origin,body.get('plan_key'),body.get('selected'))
                else:
                    raise ValueError('unknown action')
                self.continuation_send(result)
            except (ValueError,KeyError,OSError,ContractError):
                self.continuation_send(dict(message='暂时无法同步或打开下一项，请重试。已提交的答案会保留。'),400)

        def adapt(self,data,mime):
            if 'text/html' in mime:
                return data.replace(b'</head>',b'<link rel="stylesheet" href="/continuity.css"><script src="/continuity.js"></script></head>')
            if 'javascript' in mime and urlsplit(self.path).path.endswith('/app.js'):
                value={'reading':'currentPayload','course':'view','route':'state'}[family]
                pause="async()=>{}"
                if family=='course':
                    pause="async()=>stopAudio()"
                elif family=='route':
                    pause="async()=>{if(state.stage==='teaching'&&playing){audio.pause();playing=false;await act('audio_position',{ticket:state.audio.ticket,position:audio.currentTime},false);await act('audio_failed',{ticket:state.audio.ticket},false);restored=true;render();}}"
                hook=f"\nconst continuityRender=render;render=function(...args){{continuityRender(...args);window.N5Continuity.update({value},{{family:{json.dumps(family)},pause:{pause}}});}};\n"
                if family=='reading':
                    hook+="const continuityPractice=renderPractice;renderPractice=function(...args){continuityPractice(...args);window.N5Continuity.update(currentPayload,{family:'reading',pause:async()=>{}});};\n"
                return data+hook.encode()
            if urlsplit(self.path).path=='/health' and 'json' in mime:
                value=json.loads(data);value['continuation_binding']=control.binding
                return json.dumps(value).encode()
            return data

        def send_bytes(self,data,content_type,status=200):
            return super().send_bytes(self.adapt(data,content_type),content_type,status)

        def send(self,value,status=200,mime='application/json; charset=utf-8',extra_headers=None):
            if isinstance(value,bytes):
                value=self.adapt(value,mime)
            elif urlsplit(self.path).path=='/health':
                value={**value,'continuation_binding':control.binding}
            return super().send(value,status,mime,extra_headers)

    return Connected
