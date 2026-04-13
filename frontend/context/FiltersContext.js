import React, { useEffect, useState, createContext, useCallback } from 'react';
import { Api } from '../api';

export const FiltersContext = createContext({ filters: [], sync: async () => [], setFilters: () => {} });

export function FiltersProvider({ children }) {
  const [filters, setFilters] = useState([]);

  const sync = useCallback(async () => {
    try {
      const data = await Api.listFilters();
      setFilters(data);
    } catch (e) {
      console.log('filters sync error', e.message);
    }
  }, []);

  useEffect(() => {
    sync();
  }, [sync]);

  return (
    <FiltersContext.Provider value={{ filters, setFilters, sync }}>
      {children}
    </FiltersContext.Provider>
  );
}
