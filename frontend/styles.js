import { Platform, StyleSheet } from 'react-native';

const PRIMARY = '#007AFF';
const BG = '#F7F8FA';
const CARD = '#FFFFFF';
const TEXT = '#1C1C1E';
const MUTED = '#8E8E93';
const BORDER = '#E5E5EA';

export const stylesPicker = {
  iconColor: Platform.select({ ios: undefined, android: MUTED }),
};

export const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: BG,
  },
  container: {
    padding: 16,
    paddingBottom: 32,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: TEXT,
    marginBottom: 12,
  },
  section: {
    backgroundColor: CARD,
    borderRadius: 12,
    padding: 12,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: BORDER,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: TEXT,
    marginBottom: 8,
  },
  field: {
    marginBottom: 12,
  },
  label: {
    fontSize: 14,
    color: MUTED,
    marginBottom: 6,
  },
  pickerWrapper: {
    borderWidth: 1,
    borderColor: BORDER,
    borderRadius: 10,
    overflow: Platform.select({ ios: 'hidden', android: 'visible', default: 'hidden' }),
    backgroundColor: '#FAFAFB',
  },
  pickerDisabled: {
    opacity: 0.55,
  },
  picker: {
    height: Platform.select({ ios: 216, android: 44, default: 44 }),
  },
  row: {
    flexDirection: 'row',
    gap: 10,
  },
  input: {
    borderWidth: 1,
    borderColor: BORDER,
    backgroundColor: '#FAFAFB',
    borderRadius: 10,
    paddingHorizontal: 12,
    height: 44,
    color: TEXT,
  },
  inputHalf: {
    flex: 1,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  chip: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: BORDER,
    backgroundColor: '#FFFFFF',
  },
  chipSelected: {
    backgroundColor: PRIMARY,
    borderColor: PRIMARY,
  },
  chipText: {
    color: TEXT,
    fontSize: 13,
    fontWeight: '500',
  },
  chipTextSelected: {
    color: '#FFFFFF',
  },
  searchButton: {
    backgroundColor: PRIMARY,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 14,
  },
  searchButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
  },
  resultItem: {
    backgroundColor: '#FFFFFF',
    borderRadius: 10,
    padding: 12,
    borderWidth: 1,
    borderColor: BORDER,
  },
  resultItemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  thumb: {
    width: 96,
    height: 72,
    borderRadius: 8,
    backgroundColor: '#EEEEEF',
    borderWidth: 1,
    borderColor: BORDER,
  },
  deleteButton: {
    backgroundColor: '#FF3B30',
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    minWidth: 52,
    alignItems: 'center',
  },
  deleteButtonText: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  resultText: {
    color: TEXT,
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace' }),
    fontSize: 12,
  },
  separator: {
    height: 10,
  },
  resultsEmptyContainer: {
    paddingVertical: 12,
  },
  placeholderText: {
    color: MUTED,
    fontStyle: 'italic',
  },
});


