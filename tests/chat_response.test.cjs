const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../apps/chat/static/chat/chat.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.split('const form =')[0], context);
const readChatResponse = context.readChatResponse;

function response(status, type, payload) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => type },
    json: async () => payload,
  };
}

test('HTML gateway errors show the HTTP cause without attempting JSON parsing', async () => {
  for (const status of [502, 503, 504]) {
    const reply = response(status, 'text/html');
    reply.json = () => assert.fail('HTML must not be parsed as JSON');
    await assert.rejects(readChatResponse(reply), new RegExp(String(status)));
  }
});

test('JSON service error preserves the server message', async () => {
  await assert.rejects(
    readChatResponse(response(503, 'application/json', { error: '상담 처리 중 오류' })),
    /상담 처리 중 오류/,
  );
});

test('successful chat answer is preserved', async () => {
  const payload = { answer: '답변', status: 'answered' };
  assert.equal(await readChatResponse(response(200, 'application/json; charset=utf-8', payload)), payload);
});

test('malformed JSON does not expose the raw parsing exception', async () => {
  const reply = response(200, 'application/json');
  reply.json = async () => { throw new SyntaxError('Unexpected token'); };
  await assert.rejects(readChatResponse(reply), /서버 응답을 읽지 못했습니다/);
});

test('invalid JSON response shape is rejected', async () => {
  await assert.rejects(readChatResponse(response(200, 'application/json', null)), /응답 형식/);
});
