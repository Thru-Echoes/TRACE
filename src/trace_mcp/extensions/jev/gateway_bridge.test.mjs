import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateRequest, boundedFetch } from './gateway_bridge.mjs';

const body = { model: 'typesafe-ai/jev', state: { synthetic: true }, questions: {
  candidate_type: { type: 'choice', instructions: 'Classify', criteria: {
    durable_candidate: 'Durable', episodic: 'Episodic', insufficient: 'Unsure',
  } },
  bounded: { type: 'boolean', instructions: 'Bounded?' },
}, providerOptions: { gateway: { zeroDataRetention: true } } };

const response = { answers: {
  candidate_type: { type: 'choice', choice: 'durable_candidate', probabilities: {
    durable_candidate: .98, episodic: .01, insufficient: .01,
  } },
  bounded: { type: 'boolean', probability: .99 },
}, providerMetadata: { typesafe: { confidence: { candidate_type: .95 }, private: 'must-not-cross' } },
usage: { inputTokens: 12, outputTokens: 4 } };

test('official Gateway evaluation wire, normalization, and metadata boundary', async () => {
  let calls = 0;
  const result = await evaluateRequest(body, 'workspace-test-key', async (url, options) => {
    calls++;
    assert.equal(url, 'https://ai-gateway.vercel.sh/v4/ai/evaluation-model');
    assert.equal(new Headers(options.headers).get('authorization'), 'Bearer workspace-test-key');
    const wire = JSON.parse(options.body);
    assert.deepEqual(wire, { state: body.state, questions: body.questions, providerOptions: body.providerOptions });
    return Response.json(response);
  });
  assert.equal(calls, 1);
  assert.equal(result.answers.candidate_type.confidence, .95);
  assert.deepEqual(result.answers.bounded, { type: 'noul', noul: .99 });
  assert.deepEqual(result.usage, { input_tokens: 12, output_tokens: 4 });
  assert.ok(!JSON.stringify(result).includes('must-not-cross'));
});

test('missing keys and wrong models fail before network', async () => {
  const noCall = () => assert.fail('Unexpected request');
  await assert.rejects(evaluateRequest(body, '', noCall));
  await assert.rejects(evaluateRequest({ ...body, model: 'jev-latest' }, 'test', noCall));
});

test('SDK retries are disabled and response bytes are bounded', async () => {
  let calls = 0;
  await assert.rejects(evaluateRequest(body, 'test', async () => {
    calls++;
    return new Response('Unavailable', { status: 503 });
  }));
  assert.equal(calls, 1);
  const prior = globalThis.fetch;
  globalThis.fetch = async () => new Response('x'.repeat(64_001));
  try { await assert.rejects(boundedFetch('https://example.test', {}), /exceeds limit/); }
  finally { globalThis.fetch = prior; }
});
