import { expect, test } from '@playwright/test';

const WORKSPACE_ID = '00000000-0000-0000-0000-000000000003';
const CONTEXT_ID = '11111111-1111-1111-1111-111111111111';
const RUN_ID = '22222222-2222-2222-2222-222222222222';
const FILE_NAME = 'chat-file-browser-note.txt';
const CONTEXT_PATH = `/chat/uploads/${CONTEXT_ID}/${FILE_NAME}`;

type RunStartBody = {
  payload?: {
    message?: string;
    attachments?: Array<{ path?: string }>;
  };
};

test('chat file selection, preview, upload, send, and remote preview', async ({ page }) => {
  const captured = {
    uploadSeen: false,
    uploadHadFile: false,
    runStartBody: undefined as RunStartBody | undefined,
    downloadSeen: false,
  };

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();

    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });

    if (pathname === '/api/workspaces' && method === 'GET') {
      return json({
        total: 1,
        items: [
          {
            id: WORKSPACE_ID,
            name: 'Browser Test Workspace',
            description: null,
            app_id: null,
            owner_id: '00000000-0000-0000-0000-000000000001',
            visibility: 'private',
            is_shared: false,
            executor_code: null,
            executor_config: {},
            settings: {},
            status: 'active',
            run_count: 0,
            member_count: 1,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ],
        page: 1,
        page_size: 50,
      });
    }

    if (pathname === `/api/workspaces/${WORKSPACE_ID}/runs` && method === 'GET') {
      return json({ total: 0, items: [], page: 1, page_size: 50 });
    }
    if (pathname === `/api/workspaces/${WORKSPACE_ID}/tasks` && method === 'GET') {
      return json({ total: 0, items: [] });
    }
    if (pathname === `/api/workspaces/${WORKSPACE_ID}/artifacts` && method === 'GET') {
      return json({ total: 0, items: [] });
    }
    if (pathname === `/api/workspaces/${WORKSPACE_ID}/contexts` && method === 'GET') {
      return json({
        total: 1,
        items: [
          {
            id: CONTEXT_ID,
            workspace_id: WORKSPACE_ID,
            name: FILE_NAME,
            path: CONTEXT_PATH,
            content_type: 'text/plain',
            s3_key: 'workspaces/ws/chat_uploads/browser-note.txt',
            size_bytes: 45,
            meta: { source: 'chat_upload' },
            glance: `Uploaded chat file: ${FILE_NAME}`,
            tags: ['chat', 'upload', 'file'],
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        ],
        page: 1,
        page_size: 20,
      });
    }
    if (pathname === '/api/tools/' && method === 'GET') {
      return json({ total: 0, tools: [] });
    }
    if (pathname === '/api/apps/executors/list' && method === 'GET') {
      return json([]);
    }
    if (pathname === '/api/quota/me' && method === 'GET') {
      return json({
        free_quota_total: 100_000,
        free_quota_used: 0,
        paid_quota_total: 0,
        paid_quota_used: 0,
        remaining_tokens: 100_000,
      });
    }

    if (pathname === `/api/workspaces/${WORKSPACE_ID}/chat-files` && method === 'POST') {
      captured.uploadSeen = true;
      const multipart = request.postDataBuffer()?.toString('utf8') ?? '';
      captured.uploadHadFile =
        multipart.includes('files[]') &&
        multipart.includes(FILE_NAME);
      return json(
        {
          total: 1,
          items: [
            {
              id: CONTEXT_ID,
              name: FILE_NAME,
              path: CONTEXT_PATH,
              content_type: 'text/plain',
              size_bytes: 45,
              download_url: `/api/workspaces/${WORKSPACE_ID}/chat-files/${CONTEXT_ID}/download`,
              parse_status: 'parsed',
              created_at: new Date().toISOString(),
            },
          ],
        },
        201,
      );
    }

    if (pathname === `/api/workspaces/${WORKSPACE_ID}/runs` && method === 'POST') {
      captured.runStartBody = JSON.parse(request.postData() ?? '{}');
      return json(
        {
          id: RUN_ID,
          workspace_id: WORKSPACE_ID,
          app_id: null,
          user_id: '00000000-0000-0000-0000-000000000001',
          status: 'running',
          trigger_type: 'user',
          input_data: { message: 'Please review the attached file(s).' },
          output_data: {},
          waiting_for: {},
          last_event_sequence: 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
        201,
      );
    }

    if (pathname === `/api/runs/${RUN_ID}/events/stream` && method === 'GET') {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body:
          'event: run.state.change\n' +
          'data: {"event_type":"run.state.change","payload":{"previous_state":"running","new_state":"finished"},"sequence":2}\n\n',
      });
    }

    if (
      pathname === `/api/workspaces/${WORKSPACE_ID}/chat-files/${CONTEXT_ID}/download` &&
      method === 'GET'
    ) {
      captured.downloadSeen = true;
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/plain' },
        body: 'downloaded browser integration file\n',
      });
    }

    return json({});
  });

  await page.addInitScript(({ workspaceId }) => {
    localStorage.setItem('access_token', 'browser-test-token');
    localStorage.setItem(
      'workspace-storage',
      JSON.stringify({
        state: {
          currentWorkspaceId: workspaceId,
          currentWorkspaceName: 'Browser Test Workspace',
          currentWorkspaceAppId: null,
        },
        version: 0,
      }),
    );
  }, { workspaceId: WORKSPACE_ID });

  await page.goto('/app');
  await expect(page.getByRole('heading', { name: 'New chat' })).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: FILE_NAME,
    mimeType: 'text/plain',
    buffer: Buffer.from('browser integration file\nsecond line from preview\n'),
  });

  await expect(page.getByText(FILE_NAME)).toBeVisible();

  await page.getByTitle('Preview file').first().click();
  await expect(page.getByText('browser integration file')).toBeVisible();
  await page.getByRole('button', { name: 'Close' }).click();

  await page.getByTitle('Send message').click();

  await expect(page.getByText('Please review the attached file(s).')).toBeVisible();
  await expect(page.getByText(FILE_NAME)).toBeVisible();
  expect(captured.uploadSeen).toBe(true);
  expect(captured.uploadHadFile).toBe(true);
  expect(captured.runStartBody?.payload?.message).toBe('Please review the attached file(s).');
  expect(captured.runStartBody?.payload?.attachments?.[0]?.path).toBe(CONTEXT_PATH);

  const rightPanel = page.locator('aside').last();
  await page.getByRole('tab', { name: /Context/ }).click();
  await expect(rightPanel.getByText('Chat', { exact: true })).toBeVisible();
  await expect(rightPanel.getByText(FILE_NAME)).toBeVisible();

  await page.getByTitle(CONTEXT_PATH).click();
  await expect(page.getByText('downloaded browser integration file')).toBeVisible();
  expect(captured.downloadSeen).toBe(true);
});
