/**
 * Minimal OpenAI Responses-API streaming mock for E2E tests.
 *
 * `mock-server.ts` only speaks chat completions. The Responses/Codex wire
 * (`api_mode: codex_responses`, what a `gpt-*` route through a custom base URL
 * uses) differs where it matters for the desktop: mid-turn narration arrives as
 * a `phase: "commentary"` message item beside the tool calls, and the backend
 * persists it on the reasoning channel with an empty assistant `content`.
 * Anything that depends on how the desktop reconciles live interim bubbles
 * with stored history is therefore invisible to a chat-completions provider.
 *
 * Endpoints:
 *   GET  /v1/models      -> one fake model
 *   POST /v1/responses   -> a scripted streaming response
 *
 * A user message containing `E2E_RESPONSES(<name>)` runs `scripts[<name>]`.
 * The step is the number of tool results the conversation already holds after
 * that message, so every such message starts its script again and no counter is
 * shared between tests. Requests that carry no trigger (background review,
 * auxiliary calls) get a one-word final answer.
 */

import http from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'

export interface ResponsesToolCall {
  args: Record<string, unknown>
  name: string
}

export interface ResponsesStep {
  /** Mid-turn narration (`phase: commentary`), streamed ahead of the tool calls. */
  commentary?: string
  /** Reasoning summary, streamed first. */
  reasoning?: string
  /** Final answer (`phase: final_answer`). Leave empty on a step that calls tools. */
  text?: string
  toolCalls?: ResponsesToolCall[]
}

export interface ResponsesMockOptions {
  scripts: Record<string, ResponsesStep[]>
}

export interface ResponsesMock {
  close: () => Promise<void>
  port: number
  url: string
}

export const RESPONSES_TRIGGER = /E2E_RESPONSES\(([a-z0-9-]+)\)/

interface InputItem {
  content?: unknown
  role?: string
  type?: string
}

const sleep = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms))

function chunksOf(text: string, size: number): string[] {
  const out: string[] = []

  for (let index = 0; index < text.length; index += size) {
    out.push(text.slice(index, index + size))
  }

  return out
}

function itemText(content: unknown): string {
  if (typeof content === 'string') {
    return content
  }

  if (!Array.isArray(content)) {
    return ''
  }

  return content
    .map(part =>
      part && typeof part === 'object' && typeof (part as { text?: unknown }).text === 'string'
        ? (part as { text: string }).text
        : ''
    )
    .join(' ')
}

/** The newest user item of the request input, with its position. */
function lastUserItem(input: InputItem[]): { index: number; text: string } {
  for (let index = input.length - 1; index >= 0; index--) {
    if (input[index]?.role === 'user') {
      return { index, text: itemText(input[index].content) }
    }
  }

  return { index: -1, text: '' }
}

function stepFor(
  input: InputItem[],
  scripts: Record<string, ResponsesStep[]>
): { idPrefix: string; step: ResponsesStep } | null {
  const user = lastUserItem(input)
  const name = RESPONSES_TRIGGER.exec(user.text)?.[1]
  const script = name ? scripts[name] : undefined

  if (!script?.length) {
    return null
  }

  const done = input.slice(user.index + 1).filter(item => item?.type === 'function_call_output').length

  return { idPrefix: `${name}_${user.index}_${done}`, step: script[Math.min(done, script.length - 1)] }
}

async function streamStep(res: ServerResponse, model: string, step: ResponsesStep, idPrefix: string): Promise<void> {
  res.writeHead(200, { 'Cache-Control': 'no-cache', Connection: 'keep-alive', 'Content-Type': 'text/event-stream' })

  let sequence = 0
  const output: unknown[] = []
  let outputIndex = 0

  const send = (type: string, data: Record<string, unknown>): void => {
    if (!res.destroyed) {
      res.write(`event: ${type}\ndata: ${JSON.stringify({ type, sequence_number: ++sequence, ...data })}\n\n`)
    }
  }

  const message = async (text: string, phase: 'commentary' | 'final_answer'): Promise<void> => {
    const id = `msg_${idPrefix}_${outputIndex}`

    send('response.output_item.added', {
      item: { content: [], id, phase, role: 'assistant', status: 'in_progress', type: 'message' },
      output_index: outputIndex
    })

    send('response.content_part.added', {
      content_index: 0,
      item_id: id,
      output_index: outputIndex,
      part: { annotations: [], text: '', type: 'output_text' }
    })

    for (const piece of chunksOf(text, 4)) {
      send('response.output_text.delta', { content_index: 0, delta: piece, item_id: id, output_index: outputIndex })
      await sleep(15)
    }

    send('response.output_text.done', { content_index: 0, item_id: id, output_index: outputIndex, text })

    send('response.content_part.done', {
      content_index: 0,
      item_id: id,
      output_index: outputIndex,
      part: { annotations: [], text, type: 'output_text' }
    })

    const item = {
      content: [{ annotations: [], text, type: 'output_text' }],
      id,
      phase,
      role: 'assistant',
      status: 'completed',
      type: 'message'
    }

    send('response.output_item.done', { item, output_index: outputIndex })
    output.push(item)
    outputIndex++
  }

  send('response.created', {
    response: { id: `resp_${idPrefix}`, model, object: 'response', output: [], status: 'in_progress' }
  })

  if (step.reasoning) {
    const id = `rs_${idPrefix}`

    send('response.output_item.added', { item: { id, summary: [], type: 'reasoning' }, output_index: outputIndex })

    send('response.reasoning_summary_part.added', {
      item_id: id,
      output_index: outputIndex,
      part: { text: '', type: 'summary_text' },
      summary_index: 0
    })

    for (const piece of chunksOf(step.reasoning, 6)) {
      send('response.reasoning_summary_text.delta', {
        delta: piece,
        item_id: id,
        output_index: outputIndex,
        summary_index: 0
      })
      await sleep(15)
    }

    send('response.reasoning_summary_text.done', {
      item_id: id,
      output_index: outputIndex,
      summary_index: 0,
      text: step.reasoning
    })

    const item = { id, summary: [{ text: step.reasoning, type: 'summary_text' }], type: 'reasoning' }

    send('response.output_item.done', { item, output_index: outputIndex })
    output.push(item)
    outputIndex++
  }

  if (step.commentary) {
    await message(step.commentary, 'commentary')
  }

  for (const [index, call] of (step.toolCalls ?? []).entries()) {
    const id = `fc_${idPrefix}_${index}`
    const callId = `call_${idPrefix}_${index}`
    const args = JSON.stringify(call.args)

    send('response.output_item.added', {
      item: { arguments: '', call_id: callId, id, name: call.name, status: 'in_progress', type: 'function_call' },
      output_index: outputIndex
    })

    send('response.function_call_arguments.delta', { delta: args, item_id: id, output_index: outputIndex })
    send('response.function_call_arguments.done', { arguments: args, item_id: id, output_index: outputIndex })

    const item = { arguments: args, call_id: callId, id, name: call.name, status: 'completed', type: 'function_call' }

    send('response.output_item.done', { item, output_index: outputIndex })
    output.push(item)
    outputIndex++
  }

  if (step.text) {
    await message(step.text, 'final_answer')
  }

  send('response.completed', {
    response: {
      id: `resp_${idPrefix}`,
      model,
      object: 'response',
      output,
      status: 'completed',
      usage: {
        input_tokens: 120,
        input_tokens_details: { cached_tokens: 0 },
        output_tokens: 40,
        output_tokens_details: { reasoning_tokens: 0 },
        total_tokens: 160
      }
    }
  })

  res.write('data: [DONE]\n\n')
  res.end()
}

function handleResponses(
  res: ServerResponse,
  body: Record<string, unknown>,
  scripts: Record<string, ResponsesStep[]>
): void {
  const input = Array.isArray(body.input) ? (body.input as InputItem[]) : []
  const model = typeof body.model === 'string' && body.model ? body.model : 'mock-model'
  const scripted = stepFor(input, scripts)
  const step: ResponsesStep = scripted?.step ?? { text: 'ok' }
  const idPrefix = scripted?.idPrefix ?? `plain_${Date.now()}`

  if (body.stream === false) {
    res.writeHead(200, { 'Content-Type': 'application/json' })

    res.end(
      JSON.stringify({
        id: `resp_${idPrefix}`,
        model,
        object: 'response',
        output: [
          {
            content: [{ annotations: [], text: step.text ?? 'ok', type: 'output_text' }],
            id: `msg_${idPrefix}`,
            phase: 'final_answer',
            role: 'assistant',
            status: 'completed',
            type: 'message'
          }
        ],
        status: 'completed',
        usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 }
      })
    )

    return
  }

  void streamStep(res, model, step, idPrefix).catch(() => res.end())
}

export function startResponsesMock(options: ResponsesMockOptions): Promise<ResponsesMock> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req: IncomingMessage, res: ServerResponse) => {
      if (req.method === 'GET' && req.url === '/v1/models') {
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(
          JSON.stringify({
            data: [{ created: 0, id: 'mock-model', object: 'model', owned_by: 'mock' }],
            object: 'list'
          })
        )

        return
      }

      if (req.method === 'POST' && req.url?.startsWith('/v1/responses')) {
        const chunks: Buffer[] = []

        req.on('data', (chunk: Buffer) => chunks.push(chunk))

        req.on('end', () => {
          let body: Record<string, unknown> = {}

          try {
            body = JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown>
          } catch {
            // Malformed JSON: answer with the plain one-word reply below.
          }

          handleResponses(res, body, options.scripts)
        })

        req.on('error', () => {
          res.writeHead(400)
          res.end('Bad request')
        })

        return
      }

      res.writeHead(404, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'Not found' }))
    })

    server.on('error', reject)

    server.listen(0, '127.0.0.1', () => {
      const address = server.address()

      if (address === null || typeof address === 'string') {
        reject(new Error('Failed to get server address'))

        return
      }

      resolve({
        close: () =>
          new Promise((resolveClose, rejectClose) => {
            server.close(error => (error ? rejectClose(error) : resolveClose()))
            server.closeAllConnections()
          }),
        port: address.port,
        url: `http://127.0.0.1:${address.port}`
      })
    })
  })
}
