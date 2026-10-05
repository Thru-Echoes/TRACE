import { experimental_evaluate as evaluate } from 'ai';
import { createGateway } from '@ai-sdk/gateway';
import { pathToFileURL } from 'node:url';

const LIMIT = 64_000;
globalThis.AI_SDK_LOG_WARNINGS = false;

export async function boundedFetch(url, options) {
  const response = await fetch(url, options);
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Empty gateway response');
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > LIMIT) throw new Error('Gateway response exceeds limit');
      chunks.push(value);
    }
  } finally { await reader.cancel(); }
  return new Response(Buffer.concat(chunks), { status: response.status, headers: response.headers });
}

export async function evaluateRequest(body, apiKey, transport = boundedFetch) {
  if (!apiKey?.trim()) throw new Error('Gateway key is required');
  if (body.model !== 'typesafe-ai/jev') throw new Error('Unsupported evaluation model');
  const gateway = createGateway({ apiKey, fetch: transport });
  const result = await evaluate({
    model: gateway.evaluationModel(body.model), state: body.state, questions: body.questions,
    providerOptions: body.providerOptions, maxRetries: 0, abortSignal: AbortSignal.timeout(45_000),
  });
  const confidence = result.providerMetadata?.typesafe?.confidence;
  const answers = Object.fromEntries(Object.entries(result.answers).map(([id, answer]) => [id,
    answer.type === 'boolean' ? { type: 'noul', noul: answer.probability }
      : { type: 'choice', choice: answer.choice, probabilities: answer.probabilities,
          confidence: confidence?.[id] },
  ]));
  return { model: body.model, answers, usage: {
    input_tokens: result.usage.inputTokens, output_tokens: result.usage.outputTokens,
  } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const chunks = [];
    let size = 0;
    for await (const chunk of process.stdin) {
      size += chunk.length;
      if (size > LIMIT) throw new Error('Input exceeds limit');
      chunks.push(chunk);
    }
    const result = await evaluateRequest(
      JSON.parse(Buffer.concat(chunks).toString()), process.env.TRACE_JEV_GATEWAY_API_KEY,
    );
    const output = JSON.stringify(result);
    if (Buffer.byteLength(output) > LIMIT) throw new Error('Output exceeds limit');
    process.stdout.write(output);
  } catch {
    process.stderr.write('Jev Gateway evaluation failed; no advice recorded\n');
    process.exitCode = 1;
  }
}
