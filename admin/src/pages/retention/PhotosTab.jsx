import { useCallback, useEffect, useState } from 'react';
import { useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
import Grid from '@mui/material/Grid';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import LinearProgress from '@mui/material/LinearProgress';
import Tooltip from '@mui/material/Tooltip';
import CheckCircleOutlinedIcon from '@mui/icons-material/CheckCircleOutlined';
import { API_URL, httpClient, getToken } from '../../httpClient';
import rich from '../../components/Rich';
import { t } from '../../i18n';
import { notifyError } from '../../lib/notifyError';
import { useReadOnly } from '../../lib/useReadOnly';
import PhotoPreview from './PhotoPreview';
import GridPagination from '../../components/GridPagination';

// ---------------------------------------------------------------------------
// Photos tab (media library; binary preview needs the auth header -> blob)
// ---------------------------------------------------------------------------
// How many photos ride in one generate-metadata request; larger selections are
// chunked client-side so a slow vision batch can't hit the request timeout.
const META_CHUNK = 10;

// Photos shown per page. The whole library is loaded once (filtering is
// client-side); paginating the grid keeps only ~21 previews fetching at a
// time instead of every photo at once.
const PHOTOS_PER_PAGE = 21;

// Mirrors media_normalizer.VIDEO_EXTS — only the fallback when a response row
// lacks media_type; the server's classification is authoritative.
const VIDEO_EXT_RE = /\.(mp4|mov|m4v|webm|mkv|avi)$/i;

// fetch() can't report upload progress, so the media upload goes through
// XMLHttpRequest: onprogress fires as the body streams out, which is the
// signal the progress bar needs for multi-hundred-MB video batches.
const uploadWithProgress = (url, formData, onProgress) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText || '{}');
      } catch {
        // non-JSON error body — keep {}
      }
      resolve({ status: xhr.status, body });
    };
    xhr.onerror = () => reject(new Error('network'));
    xhr.send(formData);
  });

const fmtMb = (bytes) => (bytes / (1024 * 1024)).toFixed(1);

// Has this row been through the media normalizer? Mirrors the backend's own
// done-markers: a video is normalized exactly once to `.tg.mp4`
// (media_normalizer._VIDEO_NORM_SUFFIX), a photo's normalized output is always
// WebP. GIFs are deliberately left alone by the normalizer, so they never show
// the mark.
const isOptimized = (ph) => {
  const ref = (ph.storage_ref || '').toLowerCase();
  if (!ref) return false;
  if ((ph.media_type || 'photo') === 'video') return ref.endsWith('.tg.mp4');
  return ref.endsWith('.webp');
};

const PhotosTab = ({ productId }) => {
  // Managers are read-only server-side (403 on write) — pre-disable writes.
  const readOnly = useReadOnly();
  const notify = useNotify();
  const [items, setItems] = useState([]);
  const [upload, setUpload] = useState({ description: '', tags: '', level_min: 0, stage: 1, category: '' });
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  // { loaded, total } while the upload body streams out — drives the upload
  // progress bar. loaded === total means the bytes are sent and the server is
  // writing files / creating rows (shown as an indeterminate "processing" bar).
  const [uploadProgress, setUploadProgress] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [generating, setGenerating] = useState(false);
  // { done, total } while a metadata batch runs — drives the determinate
  // progress bar so the operator sees it moving and knows it hasn't stalled.
  const [genProgress, setGenProgress] = useState(null);
  // Server-side body cap for the upload request. A request over it is aborted
  // mid-upload (413), which the browser reports as a bare "failed to fetch" —
  // pre-checking here is the only way to give the operator a real message.
  const [uploadCap, setUploadCap] = useState(null);
  const [filters, setFilters] = useState({ q: '', stage: 'all', level: 'all', status: 'all', type: 'all' });
  const [page, setPage] = useState(1);
  // The product's real gate ranges — Stage 1..maxStage, Level 0..tiers-1 — so
  // the pickers below can only offer values the delivery gate can actually serve
  // (no stage 0 or 6, no VIP tier past the last one).
  const [gate, setGate] = useState({ tiers: ['none'], maxStage: 5 });

  const load = useCallback(() => {
    httpClient(`${API_URL}/admin/retention/photos?product_id=${productId}`)
      .then(({ json }) => setItems(json.items || []))
      .catch((e) => notify(e.message || t('Load failed'), { type: 'error' }));
  }, [productId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/settings?product_id=${productId}`)
      .then(({ json }) => {
        const rt = json?.resolved?.retention || {};
        setGate({
          tiers: (rt.vip_tiers || ['none']).map((t) => String(t)),
          maxStage: Math.max(1, Number(rt.max_stage) || 5),
        });
      })
      .catch(() => {});
  }, [productId]);

  useEffect(() => {
    httpClient(`${API_URL}/admin/meta`)
      .then(({ json }) => setUploadCap(json?.retention_max_upload_bytes || null))
      .catch(() => {});
  }, []);

  const stageChoices = Array.from({ length: gate.maxStage }, (_, i) => i + 1);
  const levelChoices = gate.tiers.map((t, i) => ({ value: i, label: `${i} · ${t}` }));

  const doUpload = async () => {
    if (!files.length) return;
    // The cap bounds the WHOLE request body — reject up front, because the
    // server's 413 aborts the connection mid-upload and the browser only
    // reports an opaque network error.
    if (uploadCap) {
      const total = files.reduce((sum, f) => sum + (f.size || 0), 0);
      if (total > uploadCap) {
        notify(
          t('Selected files total {mb} MB — over the {cap} MB upload limit. Upload fewer files at once.')
            .replace('{mb}', (total / (1024 * 1024)).toFixed(0))
            .replace('{cap}', (uploadCap / (1024 * 1024)).toFixed(0)),
          { type: 'error' }
        );
        return;
      }
    }
    setUploading(true);
    setUploadProgress({ loaded: 0, total: files.reduce((sum, f) => sum + (f.size || 0), 0) });
    const fd = new FormData();
    fd.append('product_id', String(productId));
    fd.append('description', upload.description);
    fd.append('tags', upload.tags);
    fd.append('level_min', String(upload.level_min));
    fd.append('stage', String(upload.stage));
    fd.append('category', upload.category);
    files.forEach((f) => fd.append('files', f));
    try {
      const { status, body } = await uploadWithProgress(
        `${API_URL}/admin/retention/photos`,
        fd,
        (loaded, total) => setUploadProgress({ loaded, total })
      );
      if (status < 200 || status >= 300) {
        notify(body.detail || t('Upload failed'), { type: 'error' });
        return;
      }
      // The popup names what was actually uploaded — "6 videos" must not read
      // as "6 photos". Counted from the server's own per-row media_type, with
      // the filename extension as the fallback for rows without it.
      const rows = body.photos || [];
      const nVideos = rows.length
        ? rows.filter((p) => (p.media_type || 'photo') === 'video').length
        : files.filter((f) => VIDEO_EXT_RE.test(f.name || '')).length;
      const nPhotos = (rows.length || files.length) - nVideos;
      let msg;
      if (nPhotos && nVideos) {
        msg = t('{p} photo(s) and {v} video(s) uploaded')
          .replace('{p}', nPhotos)
          .replace('{v}', nVideos);
      } else if (nVideos) {
        msg = t('{n} video(s) uploaded').replace('{n}', nVideos);
      } else {
        msg = t('{n} photo(s) uploaded').replace('{n}', nPhotos);
      }
      notify(msg, { type: 'success' });
      setFiles([]);
      load();
    } catch (e) {
      // Network failure: without this the rejection escapes the click handler
      // and the operator gets no feedback at all. An abort mid-upload here is
      // usually the server rejecting an over-cap body.
      notify(
        t('Upload failed — connection interrupted. The files may exceed the server upload limit.'),
        { type: 'error' }
      );
    } finally {
      setUploading(false);
      setUploadProgress(null);
    }
  };

  const patch = async (id, fields) => {
    try {
      await httpClient(`${API_URL}/admin/retention/photos/${id}`, {
        method: 'PUT',
        body: JSON.stringify(fields),
      });
      load();
    } catch (e) {
      notifyError(notify, e, t('Save failed'));
    }
  };

  const remove = async (id) => {
    if (!window.confirm(t('Delete this photo?'))) return;
    try {
      await httpClient(`${API_URL}/admin/retention/photos/${id}`, { method: 'DELETE' });
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      load();
    } catch (e) {
      notifyError(notify, e, t('Delete failed'));
    }
  };

  // --- filters (client-side over the loaded library) ---
  const visible = items.filter((ph) => {
    if (filters.status !== 'all' && Boolean(ph.active) !== (filters.status === 'active')) {
      return false;
    }
    if (filters.type !== 'all' && (ph.media_type || 'photo') !== filters.type) return false;
    if (filters.stage !== 'all' && Number(ph.stage) !== Number(filters.stage)) return false;
    if (filters.level !== 'all' && Number(ph.level_min) !== Number(filters.level)) return false;
    if (filters.q) {
      const hay = `${ph.description || ''} ${(ph.tags || []).join(' ')} ${ph.category || ''}`.toLowerCase();
      if (!hay.includes(filters.q.toLowerCase())) return false;
    }
    return true;
  });
  const stageOptions = [...new Set(items.map((ph) => Number(ph.stage)))].sort((a, b) => a - b);
  const levelOptions = [...new Set(items.map((ph) => Number(ph.level_min)))].sort((a, b) => a - b);

  // --- client-side pagination over the filtered set ---
  const pageCount = Math.max(1, Math.ceil(visible.length / PHOTOS_PER_PAGE));
  const safePage = Math.min(page, pageCount);
  const pageItems = visible.slice(
    (safePage - 1) * PHOTOS_PER_PAGE,
    safePage * PHOTOS_PER_PAGE
  );
  // A filter change can shrink the list below the current page; snap back.
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);
  const setFilter = (patch) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
  };

  // --- selection + AI metadata generation ---
  const toggleSelect = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectAllVisible = () => setSelected(new Set(visible.map((ph) => ph.id)));

  const generate = async () => {
    const ids = [...selected];
    if (!ids.length || generating) return;
    if (
      !window.confirm(
        t(
          'Generate metadata for {n} photo(s)? The AI fills the description, tags, stage and VIP level; current values are overwritten.'
        ).replace('{n}', ids.length)
      )
    ) {
      return;
    }
    setGenerating(true);
    setGenProgress({ done: 0, total: ids.length });
    let ok = 0;
    let failed = 0;
    const errors = [];
    // Each chunk fails independently — a mid-batch 500/network error must not
    // discard the counts of chunks that already succeeded server-side.
    for (let i = 0; i < ids.length; i += META_CHUNK) {
      const chunk = ids.slice(i, i + META_CHUNK);
      setGenProgress({ done: i, total: ids.length });
      try {
        const { json } = await httpClient(
          `${API_URL}/admin/retention/photos/generate-metadata?product_id=${productId}`,
          { method: 'POST', body: JSON.stringify({ ids: chunk }) }
        );
        (json.results || []).forEach((r) => {
          if (r.ok) ok += 1;
          else {
            failed += 1;
            errors.push(`#${r.id}: ${r.error}`);
          }
        });
      } catch (e) {
        failed += chunk.length;
        errors.push(e.body?.detail || e.message || t('request failed'));
      }
      setGenProgress({ done: Math.min(i + chunk.length, ids.length), total: ids.length });
    }
    try {
      if (failed) {
        notify(
          `${t('Metadata: {ok} generated, {failed} failed')
            .replace('{ok}', ok)
            .replace('{failed}', failed)} (${errors.slice(0, 3).join('; ')}${errors.length > 3 ? '…' : ''})`,
          { type: 'warning' }
        );
      } else {
        notify(t('Metadata generated for {n} photo(s)').replace('{n}', ok), {
          type: 'success',
        });
      }
      setSelected(new Set());
    } finally {
      setGenerating(false);
      setGenProgress(null);
      load();
    }
  };

  return (
    <Box>
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            {t('Upload photos & videos')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {rich(
              t(
                'Pick any number of files at once — photos and videos share one library and one delivery stream. The fields below apply to every uploaded item — leave them empty and use **Generate metadata** afterwards to have the AI fill the description, tags, explicitness stage and VIP level per item. Videos are re-encoded to a Telegram-friendly MP4 right after upload (a poster frame appears as the preview).'
              )
            )}
          </Typography>
          <Grid container spacing={1.5} sx={{ mb: 1 }}>
            <Grid size={{ xs: 12 }}>
              <TextField
                size="small"
                label={t('Description (grounds the caption the model writes)')}
                value={upload.description}
                onChange={(e) => setUpload({ ...upload, description: e.target.value })}
                fullWidth
                multiline
              />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 4 }}>
              <TextField
                size="small"
                label={t('Tags (comma-separated)')}
                value={upload.tags}
                onChange={(e) => setUpload({ ...upload, tags: e.target.value })}
                fullWidth
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3, md: 2 }}>
              <TextField
                select
                size="small"
                label={t('Level (min VIP tier)')}
                value={upload.level_min}
                onChange={(e) => setUpload({ ...upload, level_min: Number(e.target.value) })}
                helperText={t('VIP tier to unlock')}
                fullWidth
              >
                {levelChoices.map((o) => (
                  <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid size={{ xs: 6, sm: 3, md: 2 }}>
              <TextField
                select
                size="small"
                label={t('Stage (explicitness)')}
                value={upload.stage}
                onChange={(e) => setUpload({ ...upload, stage: Number(e.target.value) })}
                helperText={t('1 = softest')}
                fullWidth
              >
                {stageChoices.map((s) => (
                  <MenuItem key={s} value={s}>{`${t('Stage')} ${s}`}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 4 }}>
              <TextField
                size="small"
                label={t('Category')}
                value={upload.category}
                onChange={(e) => setUpload({ ...upload, category: e.target.value })}
                fullWidth
              />
            </Grid>
          </Grid>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button variant="outlined" component="label">
              {files.length
                ? t('{n} files chosen').replace('{n}', files.length)
                : t('Choose files')}
              <input
                hidden
                type="file"
                accept="image/*,video/*"
                multiple
                onChange={(e) => setFiles([...e.target.files])}
              />
            </Button>
            <Button variant="contained" onClick={doUpload} disabled={!files.length || uploading || readOnly}>
              {uploading ? t('Uploading…') : t('Upload')}
            </Button>
          </Stack>
          {/* Upload progress — determinate while the bytes stream out (video
              batches run to hundreds of MB), switching to an indeterminate
              "processing" bar once the body is sent and the server is writing
              files + creating the catalogue rows. */}
          {uploading && uploadProgress && (
            <Box sx={{ mt: 1.5 }}>
              <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                <Typography variant="caption" color="text.secondary">
                  {uploadProgress.loaded >= uploadProgress.total
                    ? t('Processing on the server…')
                    : t('Uploading…')}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {fmtMb(uploadProgress.loaded)} / {fmtMb(uploadProgress.total)} MB
                </Typography>
              </Stack>
              <LinearProgress
                variant={
                  uploadProgress.total && uploadProgress.loaded < uploadProgress.total
                    ? 'determinate'
                    : 'indeterminate'
                }
                value={
                  uploadProgress.total
                    ? Math.min(100, (uploadProgress.loaded / uploadProgress.total) * 100)
                    : 0
                }
              />
            </Box>
          )}
        </CardContent>
      </Card>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <TextField
              size="small"
              label={t('Search (description, tags, category)')}
              value={filters.q}
              onChange={(e) => setFilter({ q: e.target.value })}
              sx={{ minWidth: 200, flexGrow: 1 }}
            />
            <TextField
              select
              size="small"
              label={t('Stage')}
              value={filters.stage}
              onChange={(e) => setFilter({ stage: e.target.value })}
              sx={{ minWidth: 96 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              {stageOptions.map((s) => (
                <MenuItem key={s} value={String(s)}>
                  {s}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label={t('Level min')}
              value={filters.level}
              onChange={(e) => setFilter({ level: e.target.value })}
              sx={{ minWidth: 96 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              {levelOptions.map((l) => (
                <MenuItem key={l} value={String(l)}>
                  {l}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label={t('Type')}
              value={filters.type}
              onChange={(e) => setFilter({ type: e.target.value })}
              sx={{ minWidth: 96 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              <MenuItem value="photo">{t('photos')}</MenuItem>
              <MenuItem value="video">{t('videos')}</MenuItem>
            </TextField>
            <TextField
              select
              size="small"
              label={t('Status')}
              value={filters.status}
              onChange={(e) => setFilter({ status: e.target.value })}
              sx={{ minWidth: 96 }}
            >
              <MenuItem value="all">{t('all')}</MenuItem>
              <MenuItem value="active">{t('active')}</MenuItem>
              <MenuItem value="inactive">{t('inactive')}</MenuItem>
            </TextField>
          </Stack>
          {/* Action row: short, single-word buttons (the full explanation is an
              (i) tooltip) so they never wrap to a ragged two-line shape, and a
              live progress bar under the row while a batch runs. */}
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
            sx={{ mt: 1.5, '& .MuiButton-root': { whiteSpace: 'nowrap' } }}
          >
            <Button size="small" onClick={selectAllVisible} disabled={!visible.length}>
              {t('Select all')}
            </Button>
            <Button
              size="small"
              onClick={() => setSelected(new Set())}
              disabled={!selected.size}
            >
              {t('Clear selection')}
            </Button>
            <Tooltip
              title={t(
                "AI (the product's own model + API key) fills the description, tags, stage and minimum VIP level for every selected photo. Current values are overwritten."
              )}
            >
              <span>
                <Button
                  variant="contained"
                  size="small"
                  onClick={generate}
                  disabled={!selected.size || generating || readOnly}
                >
                  {generating ? t('Generating…') : t('Generate metadata')}
                  {selected.size ? ` (${selected.size})` : ''}
                </Button>
              </span>
            </Tooltip>
            {/* The shown/total counter lives here (not in the crowded filters
                row above) so it always has room and never wraps vertically. */}
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ ml: 'auto', whiteSpace: 'nowrap' }}
            >
              {t('{shown} of {total} photos')
                .replace('{shown}', visible.length)
                .replace('{total}', items.length)}
            </Typography>
          </Stack>
          {/* Progress feedback — determinate for the chunked metadata batch, so
              the operator sees N/total advance. */}
          {generating && genProgress && (
            <Box sx={{ mt: 1.5 }}>
              <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                <Typography variant="caption" color="text.secondary">
                  {t('Generating metadata…')}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {genProgress.done} / {genProgress.total}
                </Typography>
              </Stack>
              <LinearProgress
                variant="determinate"
                value={genProgress.total ? (genProgress.done / genProgress.total) * 100 : 0}
              />
            </Box>
          )}
        </CardContent>
      </Card>

      {items.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {t('No photos yet — upload the first ones above.')}
        </Typography>
      )}
      <Grid container spacing={2} alignItems="stretch">
        {pageItems.map((ph) => (
          <Grid size={{ xs: 12, sm: 6, md: 4 }} key={ph.id}>
            <Card
              sx={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                outline: selected.has(ph.id) ? '2px solid' : 'none',
                outlineColor: 'primary.main',
              }}
            >
              <CardContent sx={{ flexGrow: 1 }}>
                <Box sx={{ position: 'relative' }}>
                  <PhotoPreview photoId={ph.id} mediaType={ph.media_type} />
                  <Checkbox
                    checked={selected.has(ph.id)}
                    onChange={() => toggleSelect(ph.id)}
                    sx={{
                      position: 'absolute',
                      top: 4,
                      left: 4,
                      bgcolor: 'background.paper',
                      borderRadius: 1,
                      p: 0.25,
                      '&:hover': { bgcolor: 'background.paper' },
                    }}
                  />
                </Box>
                <Stack
                  direction="row"
                  spacing={0.5}
                  flexWrap="wrap"
                  useFlexGap
                  sx={{ mt: 1 }}
                >
                  {ph.media_type === 'video' && (
                    <Chip size="small" color="secondary" label={t('video')} />
                  )}
                  {/* Quiet done-marker: this file has been through the media
                      normalizer (WebP / Telegram MP4) and is delivery-ready. */}
                  {isOptimized(ph) && (
                    <Tooltip
                      title={
                        ph.media_type === 'video'
                          ? t('Optimized: re-encoded to a Telegram-ready MP4')
                          : t('Optimized: re-encoded to WebP for delivery')
                      }
                    >
                      <CheckCircleOutlinedIcon
                        sx={{
                          fontSize: 16,
                          color: 'success.main',
                          opacity: 0.7,
                          alignSelf: 'center',
                        }}
                      />
                    </Tooltip>
                  )}
                  <Chip size="small" variant="outlined" label={`${t('stage')} ${ph.stage}`} />
                  <Chip size="small" variant="outlined" label={`${t('level')} ${ph.level_min}+`} />
                  {(ph.tags || []).slice(0, 4).map((t) => (
                    <Chip key={t} size="small" label={t} />
                  ))}
                  {(ph.tags || []).length > 4 && (
                    <Chip size="small" label={`+${ph.tags.length - 4}`} />
                  )}
                </Stack>
                <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                  <TextField
                    size="small"
                    label={t('Description')}
                    defaultValue={ph.description || ''}
                    onBlur={(e) =>
                      e.target.value !== (ph.description || '') &&
                      patch(ph.id, { description: e.target.value })
                    }
                    fullWidth
                    multiline
                  />
                  <TextField
                    size="small"
                    label={t('Tags (comma-separated)')}
                    defaultValue={(ph.tags || []).join(', ')}
                    onBlur={(e) => {
                      const tags = e.target.value
                        .split(',')
                        .map((t) => t.trim().toLowerCase())
                        .filter(Boolean);
                      if (tags.join(',') !== (ph.tags || []).join(',')) {
                        patch(ph.id, { tags });
                      }
                    }}
                    fullWidth
                  />
                  <Stack direction="row" spacing={1}>
                    <TextField
                      select
                      size="small"
                      label={t('Level (min VIP)')}
                      value={ph.level_min}
                      onChange={(e) =>
                        Number(e.target.value) !== ph.level_min &&
                        patch(ph.id, { level_min: Number(e.target.value) })
                      }
                      fullWidth
                    >
                      {levelChoices.map((o) => (
                        <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
                      ))}
                      {!levelChoices.some((o) => o.value === ph.level_min) && (
                        <MenuItem value={ph.level_min}>{`${ph.level_min} · (?)`}</MenuItem>
                      )}
                    </TextField>
                    <TextField
                      select
                      size="small"
                      label={t('Stage')}
                      value={ph.stage}
                      onChange={(e) =>
                        Number(e.target.value) !== ph.stage &&
                        patch(ph.id, { stage: Number(e.target.value) })
                      }
                      fullWidth
                    >
                      {stageChoices.map((s) => (
                        <MenuItem key={s} value={s}>{`${t('Stage')} ${s}`}</MenuItem>
                      ))}
                      {!stageChoices.includes(ph.stage) && (
                        <MenuItem value={ph.stage}>{`${t('Stage')} ${ph.stage}`}</MenuItem>
                      )}
                    </TextField>
                  </Stack>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    justifyContent="space-between"
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <FormControlLabel
                      control={
                        <Switch
                          size="small"
                          checked={Boolean(ph.active)}
                          onChange={(e) => patch(ph.id, { active: e.target.checked })}
                        />
                      }
                      label={t('Active')}
                    />
                    {ph.telegram_file_id && <Chip size="small" label={t('cached in TG')} />}
                    <Button size="small" color="error" onClick={() => remove(ph.id)}>
                      {t('Delete')}
                    </Button>
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>
      {visible.length > 0 && (
        <GridPagination
          count={visible.length}
          page={safePage}
          perPage={PHOTOS_PER_PAGE}
          onPage={setPage}
          unit={t('photos')}
        />
      )}
    </Box>
  );
};

export default PhotosTab;
