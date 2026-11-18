/* Whether the workspace a component lives in is on show. Every screen stays mounted in its own tab (it keeps its
 * state); a screen in a background tab stops polling and refreshes when it is shown again. */
import { createContext, useContext } from 'react';
export const ViewVisible = createContext(true);
export const useViewVisible = () => useContext(ViewVisible);
