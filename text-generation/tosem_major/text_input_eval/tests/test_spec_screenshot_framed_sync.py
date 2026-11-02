"""Framed same-file screenshot READ tests, never a real ADB/model call."""
import hashlib,json,socket,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from text_input_eval.supplementary import spec_source_backed_repeat as m

class FramedSyncTests(unittest.TestCase):
    remote='/data/local/tmp/spec_screenshot_'+'a'*32+'.png'
    class Socket:
        def __init__(self,raw,chunk=3,error=None):self.raw=raw;self.chunk=chunk;self.error=error;self.sent=[];self.closed=False
        def recv(self,n):
            if self.error:raise self.error
            out=self.raw[:min(n,self.chunk)];self.raw=self.raw[len(out):];return out
        def sendall(self,raw):self.sent.append(raw)
        def settimeout(self,value):assert 0<value<=60
        def close(self):self.closed=True
    def frame(self,tag,length):return struct.pack('<4sI',tag,length)
    def transfer(self,wire,error=None,serial='emulator-5582',remote=None):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);s=self.Socket(wire,error=error)
            with patch.dict(m.os.environ,{},clear=True),patch('socket.create_connection',return_value=s) as connect:
                code=m._screenshot_sync_recv(serial,remote or self.remote,d/'payload.bin',d,'read')
            value=json.loads((d/'read.json').read_text())
            self.assertTrue(s.closed)
            self.assertEqual(connect.call_count,1)
            self.assertEqual((d/'read.wire.bin').read_bytes(),wire[:len(wire)-len(s.raw)] if not error else b'')
            return code,(d/'payload.bin').read_bytes(),value,s.sent
    def test_fragmented_data_headers_and_payload_require_done(self):
        raw=b'actual complete image bytes'
        wire=b'OKAYOKAY'+self.frame(b'DATA',10)+raw[:10]+self.frame(b'DATA',len(raw)-10)+raw[10:]+self.frame(b'DONE',0)
        code,payload,r,sent=self.transfer(wire)
        self.assertEqual((code,payload,r['done_received']),(0,raw,True))
        self.assertEqual(r['data_frame_sizes'],[10,len(raw)-10])
        self.assertEqual(r['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(sent,[b'001chost:transport:emulator-5582',b'0005sync:',b'RECV'+struct.pack('<I',len(self.remote))+self.remote.encode()])
    def test_full_payload_but_missing_done_is_failure(self):
        code,payload,r,_=self.transfer(b'OKAYOKAY'+self.frame(b'DATA',4)+b'full')
        self.assertEqual((code,payload,r['done_received']),(1,b'full',False))
        self.assertEqual(r['error_type'],'EOFError')
    def test_partial_data_kept_on_premature_eof(self):
        code,payload,r,_=self.transfer(b'OKAYOKAY'+self.frame(b'DATA',100)+b'partial')
        self.assertEqual((code,payload),(1,b'partial'));self.assertFalse(r['done_received'])
        self.assertEqual(r['data_frame_sizes'],[])
    def test_empty_file_still_requires_done(self):
        code,payload,r,_=self.transfer(b'OKAYOKAY'+self.frame(b'DONE',0))
        self.assertEqual((code,payload,r['done_received']),(0,b'',True))
    def test_remote_fail_is_not_retried_or_success(self):
        code,payload,r,sent=self.transfer(b'OKAYOKAY'+self.frame(b'FAIL',6)+b'denied')
        self.assertEqual(code,1);self.assertIn('denied',r['error_message']);self.assertEqual(len(sent),3)
    def test_handshake_fail_is_not_retried(self):
        code,payload,r,sent=self.transfer(b'FAIL0007offline')
        self.assertEqual(code,1);self.assertEqual(len(sent),1);self.assertIn('offline',r['error_message'])
    def test_timeout_does_not_start_server_or_retry(self):
        code,payload,r,sent=self.transfer(b'',error=socket.timeout('test timeout'))
        self.assertEqual(code,1);self.assertFalse(r['adb_server_started_or_restarted']);self.assertEqual(len(sent),1)
    def test_oversize_and_unknown_frames_fail_closed(self):
        for tag,length in ((b'DATA',65537),(b'JUNK',1),(b'DATA',0),(b'FAIL',65537)):
            with self.subTest(tag=tag,length=length):
                code,payload,r,_=self.transfer(b'OKAYOKAY'+self.frame(tag,length))
                self.assertEqual(code,1);self.assertEqual(payload,b'');self.assertFalse(r['done_received'])
    def test_foreign_serial_path_and_endpoint_rejected_before_socket(self):
        with tempfile.TemporaryDirectory() as tmp,patch('socket.create_connection') as conn:
            d=Path(tmp)
            for serial,path in (('emulator-5560',self.remote),('emulator-5582','/data/local/tmp/other.png')):
                with self.assertRaises(RuntimeError):m._screenshot_sync_recv(serial,path,d/'p',d,'x')
            with patch.dict(m.os.environ,{'ADB_SERVER_SOCKET':'tcp:other:5037'}):
                with self.assertRaises(RuntimeError):m._screenshot_sync_recv('emulator-5582',self.remote,d/'p',d,'x')
            conn.assert_not_called()
    def test_connection_failure_no_server_start(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(m.os.environ,{},clear=True), \
             patch('socket.create_connection',side_effect=ConnectionRefusedError('test refused')) as conn, \
             patch.object(m.subprocess,'run') as commands:
            d=Path(tmp);code=m._screenshot_sync_recv('emulator-5582',self.remote,d/'p',d,'x')
            self.assertEqual(code,1);self.assertEqual(conn.call_count,1);commands.assert_not_called()

if __name__=='__main__':unittest.main()
