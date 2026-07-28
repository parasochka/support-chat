import { t } from '../i18n';

/** Copy `text`, telling the operator either way.
 *
 *  navigator.clipboard is undefined outside a secure context and its write can
 *  be denied by permissions, so a bare `await navigator.clipboard.writeText()`
 *  in a click handler throws into an unhandled rejection and the operator gets
 *  NO feedback at all — worst on the API-keys page, where the token is shown
 *  exactly once and a silently failed copy loses it for good.
 */
export const copyText = async (notify, text, message) => {
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(text);
    notify(message || t('Copied'), { type: 'info' });
    return true;
  } catch {
    notify(t('Copy failed — select the text manually.'), { type: 'warning' });
    return false;
  }
};

export default copyText;
