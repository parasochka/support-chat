import { useEffect, useState } from 'react';

/** The value, but only after it has stopped changing for `delay` ms.
 *
 *  For search fields that drive a fetch: binding the request directly to the
 *  input state fires one request per keystroke, and since nothing cancels them
 *  a slower earlier response can land after a faster later one and overwrite
 *  the newer results. Debounce the value, key the effect on the debounced copy.
 */
export default function useDebounced(value, delay = 350) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
