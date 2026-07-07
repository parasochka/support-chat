import { API_URL, httpClient } from './httpClient';
import { getProductId, scopeParams, withProduct } from './productScope';

/**
 * Custom dataProvider mapped onto the existing FastAPI admin API.
 *
 * The backend is not a generic REST resource server (endpoints like
 * GET /admin/sessions -> {items, total} or PUT /admin/kb/content), so instead
 * of ra-data-simple-rest each react-admin resource is translated explicitly:
 *
 *   sessions      -> GET /admin/sessions (server-paginated, {items,total}),
 *                    GET /admin/session/{id}
 *   unresolved    -> GET /admin/unresolved (topic groups, flattened client-side)
 *   kb            -> GET /admin/kb/topics + GET/PUT /admin/kb/content,
 *                    POST /admin/kb/topics (upsert)
 *   kb_variables  -> GET /admin/kb/variables, PUT /admin/kb/variables/{key}
 *   users         -> GET/POST /admin/users, PUT/DELETE /admin/users/{email}
 */

const buildQuery = (params) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') q.set(k, v);
  });
  const s = q.toString();
  return s ? `?${s}` : '';
};

// Client-side pagination/sort for endpoints that return the full list.
const paginate = (rows, params) => {
  const { page = 1, perPage = 25 } = params.pagination || {};
  const { field, order } = params.sort || {};
  let data = [...rows];
  if (field) {
    data.sort((a, b) => {
      const av = a[field];
      const bv = b[field];
      if (av === bv) return 0;
      const cmp = av > bv || bv === undefined ? 1 : -1;
      return order === 'DESC' ? -cmp : cmp;
    });
  }
  return {
    data: data.slice((page - 1) * perPage, page * perPage),
    total: rows.length,
  };
};

// ---------------------------------------------------------------------------
// per-resource fetchers
// ---------------------------------------------------------------------------

const fetchTopics = async () => {
  const { json } = await httpClient(withProduct(`${API_URL}/admin/kb/topics`));
  return (json.topics || []).map((t) => ({ ...t, order: t.display_order }));
};

const fetchKbVariables = async () => {
  const { json } = await httpClient(withProduct(`${API_URL}/admin/kb/variables`));
  return (json.variables || []).map((v) => ({ ...v, id: v.key }));
};

const fetchUsers = async () => {
  const { json } = await httpClient(`${API_URL}/admin/users`);
  return (json.users || []).map((u) => ({ ...u, id: u.email }));
};

const fetchUnresolved = async (filter = {}) => {
  const query = buildQuery({ from: filter.from, to: filter.to, ...scopeParams() });
  const { json } = await httpClient(`${API_URL}/admin/unresolved${query}`);
  const rows = [];
  (json.groups || []).forEach((g) => {
    (g.sessions || []).forEach((s) => {
      rows.push({ ...s, id: s.session_id, topic: g.topic });
    });
  });
  return filter.topic ? rows.filter((r) => r.topic === filter.topic) : rows;
};

const saveTopic = async (data) => {
  const title = {};
  Object.entries(data.title || {}).forEach(([lang, value]) => {
    if (value) title[lang] = value;
  });
  const { json } = await httpClient(`${API_URL}/admin/kb/topics`, {
    method: 'POST',
    body: JSON.stringify({
      slug: data.slug,
      title,
      order: data.order ?? 0,
      active: data.active ?? true,
      product_id: data.product_id ?? getProductId() ?? null,
    }),
  });
  return json; // the upserted topic row (with id)
};

const dataProvider = {
  getList: async (resource, params) => {
    if (resource === 'sessions') {
      const f = params.filter || {};
      const { page = 1, perPage = 25 } = params.pagination || {};
      const query = buildQuery({
        page,
        from: f.from,
        to: f.to,
        topic: f.topic,
        lang: f.lang,
        status: f.status,
        escalated: f.escalated,
        q: f.q,
        min_messages: f.min_messages,
        ...scopeParams(),
      });
      const { json } = await httpClient(`${API_URL}/admin/sessions${query}`);
      // Server page size is fixed at 25; the List component uses perPage=25.
      void perPage;
      return { data: json.items || [], total: json.total || 0 };
    }
    if (resource === 'unresolved') {
      return paginate(await fetchUnresolved(params.filter), params);
    }
    if (resource === 'kb') {
      return paginate(await fetchTopics(), params);
    }
    if (resource === 'kb_variables') {
      return paginate(await fetchKbVariables(), params);
    }
    if (resource === 'users') {
      return paginate(await fetchUsers(), params);
    }
    throw new Error(`Unknown resource: ${resource}`);
  },

  getOne: async (resource, params) => {
    if (resource === 'sessions') {
      const { json } = await httpClient(`${API_URL}/admin/session/${params.id}`);
      const session = json.session || {};
      return {
        data: {
          ...session,
          id: params.id,
          messages: json.messages || [],
          logs: json.logs || [],
          events: json.events || [],
          cost_usd_total: json.cost_usd_total ?? session.cost_usd_total,
        },
      };
    }
    if (resource === 'kb') {
      const topics = await fetchTopics();
      const topic = topics.find((t) => String(t.id) === String(params.id));
      if (!topic) throw new Error('Topic not found');
      const { json } = await httpClient(
        `${API_URL}/admin/kb/content?topic_id=${params.id}`
      );
      return { data: { ...topic, content: json.content || '' } };
    }
    if (resource === 'kb_variables') {
      const rows = await fetchKbVariables();
      const row = rows.find((r) => r.id === params.id);
      if (!row) throw new Error('Variable not found');
      return { data: row };
    }
    if (resource === 'users') {
      const rows = await fetchUsers();
      const row = rows.find((r) => r.id === params.id);
      if (!row) throw new Error('User not found');
      return { data: row };
    }
    if (resource === 'unresolved') {
      const rows = await fetchUnresolved();
      const row = rows.find((r) => r.id === params.id);
      if (!row) throw new Error('Session not found');
      return { data: row };
    }
    throw new Error(`Unknown resource: ${resource}`);
  },

  getMany: async (resource, params) => {
    const results = await Promise.all(
      params.ids.map((id) => dataProvider.getOne(resource, { id }))
    );
    return { data: results.map((r) => r.data) };
  },

  getManyReference: async () => ({ data: [], total: 0 }),

  create: async (resource, params) => {
    if (resource === 'kb') {
      const topic = await saveTopic(params.data);
      if (params.data.content) {
        await httpClient(`${API_URL}/admin/kb/content`, {
          method: 'PUT',
          body: JSON.stringify({ topic_id: topic.id, content: params.data.content }),
        });
      }
      return { data: { ...params.data, ...topic } };
    }
    if (resource === 'users') {
      const { json } = await httpClient(`${API_URL}/admin/users`, {
        method: 'POST',
        body: JSON.stringify({
          email: params.data.email,
          password: params.data.password,
          role: params.data.role || 'manager',
        }),
      });
      return { data: { ...json.user, id: json.user.email } };
    }
    throw new Error(`Create is not supported for: ${resource}`);
  },

  update: async (resource, params) => {
    if (resource === 'kb') {
      const topic = await saveTopic({ ...params.previousData, ...params.data });
      if (params.data.content !== undefined) {
        if (params.data.content) {
          await httpClient(`${API_URL}/admin/kb/content`, {
            method: 'PUT',
            body: JSON.stringify({
              topic_id: Number(params.id),
              content: params.data.content,
            }),
          });
        } else if (params.previousData.content) {
          await httpClient(`${API_URL}/admin/kb/content?topic_id=${params.id}`, {
            method: 'DELETE',
          });
        }
      }
      return { data: { ...params.data, ...topic, content: params.data.content } };
    }
    if (resource === 'kb_variables') {
      const { json } = await httpClient(
        `${API_URL}/admin/kb/variables/${encodeURIComponent(params.id)}`,
        {
          method: 'PUT',
          body: JSON.stringify({
            key: params.id,
            description: params.data.description || '',
            value: params.data.value || '',
          }),
        }
      );
      return { data: { ...json.variable, id: json.variable.key } };
    }
    if (resource === 'users') {
      const body = {};
      if (params.data.password) body.password = params.data.password;
      if (params.data.role !== params.previousData.role) body.role = params.data.role;
      if (params.data.active !== params.previousData.active) body.active = params.data.active;
      const { json } = await httpClient(
        `${API_URL}/admin/users/${encodeURIComponent(params.id)}`,
        { method: 'PUT', body: JSON.stringify(body) }
      );
      return { data: { ...json.user, id: json.user.email } };
    }
    throw new Error(`Update is not supported for: ${resource}`);
  },

  updateMany: async () => {
    throw new Error('updateMany is not supported');
  },

  delete: async (resource, params) => {
    if (resource === 'users') {
      await httpClient(`${API_URL}/admin/users/${encodeURIComponent(params.id)}`, {
        method: 'DELETE',
      });
      return { data: params.previousData };
    }
    throw new Error(`Delete is not supported for: ${resource}`);
  },

  deleteMany: async (resource, params) => {
    if (resource === 'users') {
      await Promise.all(
        params.ids.map((id) => dataProvider.delete(resource, { id }))
      );
      return { data: params.ids };
    }
    throw new Error(`Delete is not supported for: ${resource}`);
  },
};

export default dataProvider;
