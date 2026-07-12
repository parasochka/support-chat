import { t } from './i18n';

/**
 * The support-chat "Content" hub: four surfaces that all shape what the bot
 * KNOWS and SAYS — the knowledge base, the pages it may link to, the prompt
 * variables, and the player-facing copy. They used to be four separate sidebar
 * entries; grouping them under one shared tab strip (one sidebar entry) keeps
 * the sidebar readable while every surface stays one click away. Active-tab
 * matching is by path prefix, so /kb_variables still highlights "Knowledge
 * base" and /prompt?tab=variables still highlights "Prompt".
 */
export const CONTENT_TABS = [
  { path: '/kb', label: t('Knowledge base') },
  { path: '/site-map', label: t('Site map') },
  { path: '/prompt', label: t('Prompt') },
  { path: '/translations', label: t('Translations') },
];
