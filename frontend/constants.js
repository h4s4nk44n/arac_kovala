// --- Mappings for URL parameters ---
export const FUEL_MAP = {
  'Benzinli': 'benzinli',
  'Dizel': 'dizel',
  'Benzin & LPG': 'benzin-lpg',
  'Hibrit': 'hibrit',
  'Elektrikli': 'elektrikli',
};

export const TRANSMISSION_MAP = {
  'Manuel': 'manuel',
  'Otomatik': 'otomatik',
};

export const BODY_MAP = {
  'Sedan': '250',
  'Hatchback': '62068', // 5 door
  'Station Wagon': '19',
  'Coupe': '48626',
  'Cabrio': '240',
};

// Static option lists shown in the UI
export const FUEL_OPTIONS = Object.keys(FUEL_MAP);
export const TRANSMISSION_OPTIONS = Object.keys(TRANSMISSION_MAP);
export const BODY_OPTIONS = Object.keys(BODY_MAP);
