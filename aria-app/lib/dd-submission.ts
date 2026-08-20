export type DDSubmissionState =
  | { status: 'idle'; message: ''; runId?: undefined }
  | { status: 'started'; message: string; runId: string }
  | { status: 'existing'; message: string; runId?: undefined }
  | { status: 'error'; message: string; runId?: undefined };

export interface DDSubmissionInput {
  name: string;
  jurisdiction: string;
  mode: string;
}

type DDRequest = (path: string, init?: RequestInit) => Promise<unknown>;

interface DDStartResponse {
  run_id?: unknown;
  status?: unknown;
  async_mode?: unknown;
  existing_case?: unknown;
  message?: unknown;
}

/** Submit a DD request through the backend's immediate async contract. */
export async function submitDD(request: DDRequest, input: DDSubmissionInput): Promise<DDSubmissionState> {
  const name = input.name.trim();
  if (!name) return { status: 'error', message: 'Enter an entity or company name.' };
  const mode = input.mode === 'deep' ? 'deep' : 'standard';
  const jurisdiction = input.jurisdiction.trim();
  try {
    const raw = await request('/api/aria/dd/orchestrate', {
      method: 'POST',
      body: JSON.stringify({ name, ...(jurisdiction ? { jurisdiction } : {}), mode, type: 'company', async_mode: true }),
    }) as DDStartResponse;
    if (raw.existing_case === true) {
      const message = typeof raw.message === 'string' && raw.message.trim()
        ? raw.message : `A due-diligence report already exists for ${name}.`;
      return { status: 'existing', message };
    }
    if (raw.async_mode === true && raw.status === 'running' && typeof raw.run_id === 'string' && raw.run_id) {
      return { status: 'started', message: `Due diligence started for ${name}.`, runId: raw.run_id };
    }
    return { status: 'error', message: 'ARIA could not verify that due diligence started. Please try again.' };
  } catch {
    return { status: 'error', message: 'Due diligence could not be started. Please try again.' };
  }
}
