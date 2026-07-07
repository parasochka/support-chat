/**
 * Selected product scope for the whole SPA (the multi-tenancy switcher).
 *
 * The selection persists in localStorage; changing it reloads the page so
 * every view re-fetches in the new scope (mirrors the legacy SPA behaviour).
 * No selection = the backend's default resolution (default product for
 * KB/prompt/settings endpoints, "everything I may read" for dashboards).
 */

const KEY = 'admin_product_id';
const PARTNER_KEY = 'admin_partner_id';
const NAME_KEY = 'admin_scope_name';

export const getProductId = () => {
  const v = localStorage.getItem(KEY);
  return v ? Number(v) : null;
};

export const getPartnerId = () => {
  const v = localStorage.getItem(PARTNER_KEY);
  return v ? Number(v) : null;
};

/** Human label of the current selection (product/partner name), for banners. */
export const getScopeName = () => localStorage.getItem(NAME_KEY) || '';

export const setScope = ({ productId = null, partnerId = null, name = '' }) => {
  if (productId) localStorage.setItem(KEY, String(productId));
  else localStorage.removeItem(KEY);
  if (partnerId) localStorage.setItem(PARTNER_KEY, String(partnerId));
  else localStorage.removeItem(PARTNER_KEY);
  if (name) localStorage.setItem(NAME_KEY, name);
  else localStorage.removeItem(NAME_KEY);
};

/** Query params ({product_id} / {partner_id}) for the current selection. */
export const scopeParams = () => {
  const p = {};
  const productId = getProductId();
  const partnerId = getPartnerId();
  if (productId) p.product_id = productId;
  else if (partnerId) p.partner_id = partnerId;
  return p;
};

/** product_id-only params (for endpoints that scope per product, not partner). */
export const productParams = () => {
  const productId = getProductId();
  return productId ? { product_id: productId } : {};
};

/** Append the product_id to a URL when a product is selected. */
export const withProduct = (url) => {
  const productId = getProductId();
  if (!productId) return url;
  return url + (url.includes('?') ? '&' : '?') + `product_id=${productId}`;
};
