import { useCallback, useEffect, useMemo, useState } from 'react';
import { Title, useNotify, usePermissions } from 'react-admin';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { API_URL, httpClient } from '../httpClient';
import { t } from '../i18n';
import rich from '../components/Rich';
import { notifyError } from '../lib/notifyError';
import { compactTableSx } from '../lib/table';

const mono = { fontFamily: 'monospace', fontSize: 13 };

const KEY_PLACEHOLDER = 'sak_…';

/** A copyable code block — the page is mostly snippets, so this carries it. */
const Snippet = ({ text, label }) => {
  const notify = useNotify();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      notify(t('Copied'), { type: 'info' });
    } catch {
      notify(t('Copy failed — select the text manually.'), { type: 'warning' });
    }
  };
  return (
    <Box sx={{ position: 'relative', mb: 1 }}>
      {label && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.5 }}>
          {label}
        </Typography>
      )}
      <Box
        component="pre"
        sx={{
          ...mono,
          m: 0,
          p: 1.5,
          pr: 6,
          borderRadius: 1,
          bgcolor: 'action.hover',
          overflowX: 'auto',
          whiteSpace: 'pre',
        }}
      >
        {text}
      </Box>
      <Button
        size="small"
        startIcon={<ContentCopyIcon fontSize="small" />}
        onClick={copy}
        sx={{ position: 'absolute', top: label ? 22 : 4, right: 4 }}
      >
        {t('Copy')}
      </Button>
    </Box>
  );
};

/**
 * System → MCP: wire an AI agent (Claude Code and friends) to this admin API
 * over the Model Context Protocol.
 *
 * The MCP server lives in the repo (`mcp_server/`) and is a plain CLIENT of the
 * endpoints this panel uses: it authenticates with a service key (`sak_…`), so
 * an agent sees and changes exactly what that key's role × scope allows, and
 * every write lands in the audit trail under the key's name. This page mints
 * the key and prints the config; the tool list is fetched from the server so it
 * can never drift from the code.
 */
const Mcp = () => {
  const notify = useNotify();
  const { permissions } = usePermissions();
  const [manifest, setManifest] = useState(null);
  const [denied, setDenied] = useState(false);
  const [structure, setStructure] = useState(null);
  const [token, setToken] = useState('');
  const [keyName, setKeyName] = useState('MCP agent');
  const [role, setRole] = useState('admin');
  const [productId, setProductId] = useState('');
  const [allowWrites, setAllowWrites] = useState(true);
  const [showPii, setShowPii] = useState(false);

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/mcp/manifest`)
      .then(({ json }) => setManifest(json))
      .catch((e) => {
        if (e.status === 403) setDenied(true);
        else notify(e.message || t('Load failed'), { type: 'error' });
      });
    httpClient(`${API_URL}/admin/structure`)
      .then(({ json }) => setStructure(json))
      .catch(() => setStructure(null));
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const products = useMemo(
    () =>
      (structure?.partners || []).flatMap((pa) =>
        (pa.products || []).map((pr) => ({ ...pr, partner_name: pa.name }))
      ),
    [structure]
  );

  const origin = window.location.origin;
  const env = manifest?.env || {};

  const envLines = useMemo(() => {
    const lines = [
      [env.url || 'SUPPORT_ADMIN_URL', origin],
      [env.key || 'SUPPORT_ADMIN_KEY', token || KEY_PLACEHOLDER],
    ];
    if (productId) lines.push([env.product_id || 'SUPPORT_ADMIN_PRODUCT_ID', String(productId)]);
    if (!allowWrites) lines.push([env.allow_writes || 'SUPPORT_ADMIN_ALLOW_WRITES', '0']);
    if (showPii) lines.push([env.redact_pii || 'SUPPORT_ADMIN_REDACT_PII', '0']);
    return lines;
  }, [env, origin, token, productId, allowWrites, showPii]);

  const mcpJson = useMemo(() => {
    const envObj = {};
    envLines.forEach(([k, v]) => {
      envObj[k] = v;
    });
    return JSON.stringify(
      { mcpServers: { 'support-admin': { command: 'python3', args: ['-m', 'mcp_server'], env: envObj } } },
      null,
      2
    );
  }, [envLines]);

  const cliCommand = useMemo(() => {
    const envFlags = envLines.map(([k, v]) => `-e ${k}=${v}`).join(' ');
    return `claude mcp add support-admin ${envFlags} -- python3 -m mcp_server`;
  }, [envLines]);

  const create = async () => {
    try {
      const { json } = await httpClient(`${API_URL}/admin/api-keys`, {
        method: 'POST',
        body: JSON.stringify({
          name: keyName.trim() || 'MCP agent',
          role,
          scope_type: 'global',
        }),
      });
      setToken(json.token);
      notify(t('Key created — the token is shown once, copy the config below.'), {
        type: 'success',
      });
    } catch (e) {
      notifyError(notify, e, t('Create failed'));
    }
  };

  if (permissions !== 'admin' || denied) {
    return (
      <Box sx={{ p: 2 }}>
        <Title title="MCP" />
        <Alert severity="info" sx={{ maxWidth: 720 }}>
          <AlertTitle>{t('Global administrators only')}</AlertTitle>
          {t(
            'Connecting an agent mints a credential over the whole deployment, so this page is limited to accounts with global scope.'
          )}
        </Alert>
      </Box>
    );
  }

  if (!manifest) return <Box sx={{ p: 2 }}>{t('Loading…')}</Box>;

  const groups = manifest.groups || [];
  const tools = (manifest.tools || []).filter((tool) => allowWrites || !tool.writes);

  return (
    <Box sx={{ p: 2 }}>
      <Title title="MCP" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rich(
          t(
            'MCP (Model Context Protocol) lets an AI agent — Claude Code, for instance — call this admin API directly: read the runtime logs, inspect the assembled prompt of any product, look through conversations, and (optionally) edit the knowledge base, prompt variables and settings. The server ships in the repository under `mcp_server/`; it authenticates with a service key, so the agent sees exactly what that key allows and every change is recorded in System → Logs → Activity.'
          )
        )}
      </Typography>

      {/* ---------------------------------------------------------------- */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('1. Mint a key for the agent')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t(
              'The key is created with global scope so the agent can read the deploy-wide system logs (they have no product to scope by) and switch between products on its own. Choose the role: admin lets it edit content and settings, manager makes it strictly read-only.'
            )}
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <TextField
              size="small"
              label={t('Key name')}
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              sx={{ flex: '1 1 200px', maxWidth: 320 }}
            />
            <TextField
              select
              size="small"
              label={t('Role')}
              value={role}
              onChange={(e) => setRole(e.target.value)}
              sx={{ minWidth: 220 }}
            >
              <MenuItem value="admin">{t('admin (read + write)')}</MenuItem>
              <MenuItem value="manager">{t('manager (read-only)')}</MenuItem>
            </TextField>
            <Button variant="contained" size="small" onClick={create}>
              {t('Create key')}
            </Button>
          </Stack>
          {token && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              {t(
                'The token below is shown ONCE and cannot be recovered. Copy the configuration now; if it is lost, delete the key in System → API keys and mint a new one.'
              )}
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* ---------------------------------------------------------------- */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('2. Options')}
          </Typography>
          <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <TextField
              select
              size="small"
              label={t('Default product (optional)')}
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              sx={{ minWidth: 260 }}
              helperText={t('Used when the agent calls a tool without a product_id.')}
            >
              <MenuItem value="">{t('— none, the agent picks each time —')}</MenuItem>
              {products.map((pr) => (
                <MenuItem key={pr.id} value={pr.id}>
                  {pr.partner_name} · {pr.name} (#{pr.id})
                </MenuItem>
              ))}
            </TextField>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={allowWrites}
                  onChange={(e) => setAllowWrites(e.target.checked)}
                />
              }
              label={t('Expose the write tools')}
            />
            <FormControlLabel
              control={
                <Switch size="small" checked={showPii} onChange={(e) => setShowPii(e.target.checked)} />
              }
              label={t('Show player names and emails')}
            />
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
            {t(
              'With the write tools hidden the agent never even sees them. Player names and emails are masked in transcripts and session lists by default; the messages themselves are always shown.'
            )}
          </Typography>
        </CardContent>
      </Card>

      {/* ---------------------------------------------------------------- */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('3. Point the agent at it')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {rich(
              t(
                'Run the server from a checkout of the repository (it needs `python3` and `httpx`, nothing else). Either drop this into `.mcp.json` at the project root, or run the CLI command below once.'
              )
            )}
          </Typography>
          <Snippet label=".mcp.json" text={mcpJson} />
          <Snippet label={t('or, one-off from the terminal')} text={cliCommand} />
          {!token && (
            <Typography variant="caption" color="text.secondary">
              {t('Create a key above and the real token appears in these snippets.')}
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* ---------------------------------------------------------------- */}
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('What the agent can do')}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
            <Chip size="small" label={`${tools.length} ${t('tools')}`} />
            <Chip
              size="small"
              color="warning"
              variant="outlined"
              label={`${tools.filter((x) => x.writes).length} ${t('write')}`}
            />
          </Stack>
          {groups.map((g) => {
            const rows = tools.filter((tool) => tool.group === g.key);
            if (!rows.length) return null;
            return (
              <Accordion key={g.key} disableGutters>
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography variant="subtitle2">
                    {g.label} · {rows.length}
                  </Typography>
                </AccordionSummary>
                <AccordionDetails sx={{ p: 0 }}>
                  <Box sx={{ overflowX: 'auto' }}>
                    <Table size="small" sx={compactTableSx}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('Tool')}</TableCell>
                          <TableCell>{t('What it does')}</TableCell>
                          <TableCell>{t('Endpoint')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {rows.map((tool) => (
                          <TableRow key={tool.name} hover>
                            <TableCell sx={{ ...mono, whiteSpace: 'nowrap', verticalAlign: 'top' }}>
                              {tool.name}
                              {tool.writes && (
                                <Chip
                                  size="small"
                                  color="warning"
                                  variant="outlined"
                                  label={t('write')}
                                  sx={{ ml: 1 }}
                                />
                              )}
                            </TableCell>
                            <TableCell>
                              {tool.description}
                              {tool.note && (
                                <Typography variant="caption" color="text.secondary" display="block">
                                  {tool.note}
                                </Typography>
                              )}
                            </TableCell>
                            <TableCell sx={{ ...mono, whiteSpace: 'nowrap', verticalAlign: 'top' }}>
                              {tool.method} {tool.path}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </Box>
                </AccordionDetails>
              </Accordion>
            );
          })}
        </CardContent>
      </Card>

      {/* ---------------------------------------------------------------- */}
      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Good to know')}
          </Typography>
          <Stack spacing={1}>
            <Typography variant="body2" color="text.secondary">
              {rich(
                t(
                  '**The key is the real boundary.** The agent has no database access — it calls the same endpoints this panel does, so its role × scope is enforced server-side. A manager key physically cannot write.'
                )
              )}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {rich(
                t(
                  '**Destructive operations are not exposed.** There is no tool for deleting users, products, sessions or media, for rotating widget keys or for reading secrets — the API only ever returns whether a secret is set.'
                )
              )}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {rich(
                t(
                  '**Everything is traceable.** Each write arrives as `apikey:<name>` in System → Logs → Activity, so an agent-made change is distinguishable from a human one.'
                )
              )}
            </Typography>
            <Divider />
            <Typography variant="body2" color="text.secondary">
              {t(
                'Revoke access at any time by switching the key off or deleting it in System → API keys — it applies on the agent’s very next request.'
              )}
            </Typography>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
};

export default Mcp;
