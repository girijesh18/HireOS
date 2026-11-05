// A page's tab kept in the URL's second hash segment (#/<view>/<tab>), so a
// refresh or a shared link reopens the same tab. replaceState: switching tabs
// doesn't fire hashchange (App would re-route) or pile up history entries.
import { useEffect, useState } from 'react'

const parts = () => window.location.hash.replace(/^#\/?/, '').split('/')
const read = (fallback) => parts()[1] || fallback

export function useHashTab(fallback) {
  const [tab, setTabState] = useState(() => read(fallback))
  useEffect(() => {
    const onHash = () => setTabState(read(fallback))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [fallback])
  const setTab = (t) => {
    setTabState(t)
    window.history.replaceState(null, '', `#/${parts()[0]}/${t}`)
  }
  return [tab, setTab]
}
