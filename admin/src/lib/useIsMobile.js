import useMediaQuery from '@mui/material/useMediaQuery';

/**
 * True below the MUI `sm` breakpoint (~600px). Shared by the list views
 * (Datagrid → SimpleList fallback) and the dialogs (fullScreen on phones) so
 * every surface flips to its mobile layout at the same width.
 */
export default function useIsMobile() {
  return useMediaQuery((theme) => theme.breakpoints.down('sm'), { noSsr: true });
}
