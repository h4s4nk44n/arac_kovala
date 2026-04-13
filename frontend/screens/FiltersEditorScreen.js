import React, { useEffect, useState, useContext } from 'react';
import { SafeAreaView, ScrollView, View, Text, TextInput, Pressable, Platform, Alert } from 'react-native';
import { Picker } from '@react-native-picker/picker';
import { useNavigation } from '@react-navigation/native';

import { styles, stylesPicker } from '../styles';
import { Api } from '../api';
import { FiltersContext } from '../context/FiltersContext';
import {
  FUEL_MAP, TRANSMISSION_MAP, BODY_MAP,
  FUEL_OPTIONS, TRANSMISSION_OPTIONS, BODY_OPTIONS,
} from '../constants';

// Load local JSON datasets by car type
const otomobilData = require('../otomobil.json');
const araziSuvPickupData = require('../arazi_suv_pickup.json');

export default function FiltersEditorScreen() {
  const navigation = useNavigation();
  const { sync } = useContext(FiltersContext);
  // Dataset and picker lists
  const [carType, setCarType] = useState('Otomobil');
  const [dataset, setDataset] = useState(otomobilData);
  const [brandOptions, setBrandOptions] = useState(Object.keys(otomobilData || {}).sort());
  const [seriesOptions, setSeriesOptions] = useState([]);
  const [modelOptions, setModelOptions] = useState([]);

  // Selected values
  const [selectedBrand, setSelectedBrand] = useState('');
  const [selectedSeries, setSelectedSeries] = useState('');
  const [selectedModel, setSelectedModel] = useState('');

  // Range filters
  const [priceMin, setPriceMin] = useState('');
  const [priceMax, setPriceMax] = useState('');
  const [yearMin, setYearMin] = useState('');
  const [yearMax, setYearMax] = useState('');
  const [kmMin, setKmMin] = useState('');
  const [kmMax, setKmMax] = useState('');

  // Option filters
  const [selectedFuels, setSelectedFuels] = useState([]);
  const [selectedTransmissions, setSelectedTransmissions] = useState([]);
  const [selectedBodies, setSelectedBodies] = useState([]);

  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (dataset && typeof dataset === 'object') {
      setBrandOptions(Object.keys(dataset).sort());
    } else {
      setBrandOptions([]);
    }
  }, [dataset]);

  const handleCarTypeChange = (value) => {
    setCarType(value);
    const nextDataset = value === 'Otomobil' ? otomobilData : araziSuvPickupData;
    setDataset(nextDataset);
    setSelectedBrand('');
    setSelectedSeries('');
    setSelectedModel('');
    setSeriesOptions([]);
    setModelOptions([]);
  };

  const handleBrandChange = (value) => {
    setSelectedBrand(value);
    setSelectedSeries('');
    setSelectedModel('');
    const nextSeries = value ? Object.keys(dataset?.[value]?.series || {}).sort() : [];
    setSeriesOptions(nextSeries);
    setModelOptions([]);
  };

  const handleSeriesChange = (value) => {
    setSelectedSeries(value);
    setSelectedModel('');
    const nextModels =
      selectedBrand && value
        ? Object.keys(dataset?.[selectedBrand]?.series?.[value]?.models || {}).sort()
        : [];
    setModelOptions(nextModels);
  };

  const handleModelChange = (value) => {
    setSelectedModel(value);
  };

  const toggleSelection = (currentList, setList, value) => {
    if (currentList.includes(value)) {
      setList(currentList.filter((v) => v !== value));
    } else {
      setList([...currentList, value]);
    }
  };

  const onlyDigits = (text) => (text || '').replace(/\D+/g, '');
  const formatThousands = (digits) => {
    if (!digits) return '';
    const n = parseInt(digits, 10);
    if (isNaN(n)) return '';
    return n.toLocaleString('tr-TR');
  };

  const onChangePriceMin = (text) => setPriceMin(onlyDigits(text));
  const onChangePriceMax = (text) => setPriceMax(onlyDigits(text));
  const onChangeKmMin = (text) => setKmMin(onlyDigits(text));
  const onChangeKmMax = (text) => setKmMax(onlyDigits(text));

  // --- UPDATED: This function now builds the complete, filtered URL ---
  const resolveSelectedUrl = () => {
    if (!selectedBrand) return null;
    const brandObj = dataset?.[selectedBrand];
    if (!brandObj) return null;

    // 1. Get the base URL from the JSON data
    let baseUrl = brandObj.url;
    if (selectedSeries) {
      const seriesObj = brandObj.series?.[selectedSeries];
      if (seriesObj?.url) baseUrl = seriesObj.url;
      if (selectedModel) {
        const modelObj = seriesObj?.models?.[selectedModel];
        if (modelObj?.url) baseUrl = modelObj.url;
      }
    }

    // 2. Build path segments for fuel and transmission
    let pathSegments = [];
    if (selectedFuels.length > 0) {
        pathSegments.push(selectedFuels.map(f => FUEL_MAP[f]).join('+'));
    }
    if (selectedTransmissions.length > 0) {
        pathSegments.push(selectedTransmissions.map(t => TRANSMISSION_MAP[t]).join('+'));
    }

    let finalUrl = baseUrl;
    if (pathSegments.length > 0) {
        finalUrl += '/' + pathSegments.join('/');
    }

    // 3. Build query parameters for ranges and body types
    const params = new URLSearchParams();
    if (priceMin) params.append('price_min', priceMin);
    if (priceMax) params.append('price_max', priceMax);
    if (yearMin) params.append('a5_min', yearMin);
    if (yearMax) params.append('a5_max', yearMax);
    if (kmMin) params.append('a4_min', kmMin);
    if (kmMax) params.append('a4_max', kmMax);

    selectedBodies.forEach(body => {
        if (BODY_MAP[body]) {
            params.append('a8', BODY_MAP[body]);
        }
    });

    // Always add sorting
    params.append('sorting', 'date_desc');

    // 4. Combine and return the final URL
    const queryString = params.toString();
    if (queryString) {
        return `${finalUrl}?${queryString}`;
    }
    return finalUrl;
  };

  const handleSearch = async () => {
    try {
      if (!selectedBrand) {
        Alert.alert('Uyarı', 'Lütfen bir marka seçiniz.');
        return;
      }
      const url = resolveSelectedUrl();
      if (!url) {
        Alert.alert('Uyarı', 'Seçimlerden URL oluşturulamadı.');
        return;
      }
      const nameParts = [selectedBrand];
      if (selectedSeries) nameParts.push(selectedSeries);
      if (selectedModel) nameParts.push(selectedModel);
      const name = nameParts.join(' / ');

      setCreating(true);
      const created = await Api.createFilter({ name, url });
      Alert.alert('Kaydedildi', `${created.name} filtresi eklendi.`);
      await sync();
      navigation.navigate('Filtreler');
    } catch (e) {
      console.log('Create filter error:', e);
      Alert.alert('Hata', e.message || 'Filtre kaydedilemedi');
    } finally {
      setCreating(false);
    }
  };

  const DisabledWrapper = ({ disabled, children }) => (
    <View style={[styles.pickerWrapper, disabled && styles.pickerDisabled]}>
      {React.cloneElement(children, { enabled: !disabled })}
    </View>
  );

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Araç Filtreleme</Text>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Araç Tipi</Text>
          <View style={styles.field}>
            <Text style={styles.label}>Tip</Text>
            <View style={styles.pickerWrapper}>
              <Picker
                selectedValue={carType}
                onValueChange={handleCarTypeChange}
                dropdownIconColor={stylesPicker.iconColor}
                style={styles.picker}
                mode={Platform.OS === 'android' ? 'dropdown' : undefined}
              >
                <Picker.Item label="Otomobil" value="Otomobil" />
                <Picker.Item label="Arazi-SUV-Pickup" value="Arazi-Suv-Pickup" />
              </Picker>
            </View>
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Marka / Seri / Model</Text>
          <View style={styles.field}>
            <Text style={styles.label}>Marka</Text>
            <DisabledWrapper disabled={false}>
              <Picker
                selectedValue={selectedBrand}
                onValueChange={handleBrandChange}
                dropdownIconColor={stylesPicker.iconColor}
                style={styles.picker}
                enabled={true}
                mode={Platform.OS === 'android' ? 'dropdown' : undefined}
              >
                <Picker.Item label="Seçiniz..." value="" color="#888" />
                {brandOptions.map((brand) => (
                  <Picker.Item key={brand} label={brand} value={brand} />
                ))}
              </Picker>
            </DisabledWrapper>
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>Seri</Text>
            <DisabledWrapper disabled={!selectedBrand}>
              <Picker
                selectedValue={selectedSeries}
                onValueChange={handleSeriesChange}
                dropdownIconColor={stylesPicker.iconColor}
                style={styles.picker}
                enabled={!!selectedBrand}
                mode={Platform.OS === 'android' ? 'dropdown' : undefined}
              >
                <Picker.Item label="Seçiniz..." value="" color="#888" />
                {seriesOptions.map((series) => (
                  <Picker.Item key={series} label={series} value={series} />
                ))}
              </Picker>
            </DisabledWrapper>
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>Model</Text>
            <DisabledWrapper disabled={!selectedSeries}>
              <Picker
                selectedValue={selectedModel}
                onValueChange={handleModelChange}
                dropdownIconColor={stylesPicker.iconColor}
                style={styles.picker}
                enabled={!!selectedSeries}
                mode={Platform.OS === 'android' ? 'dropdown' : undefined}
              >
                <Picker.Item label="Seçiniz..." value="" color="#888" />
                {modelOptions.map((model) => (
                  <Picker.Item key={model} label={model} value={model} />
                ))}
              </Picker>
            </DisabledWrapper>
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Aralık Filtreleri</Text>
          <View style={styles.field}>
            <Text style={styles.label}>Fiyat (₺)</Text>
            <View style={styles.row}>
              <TextInput
                value={formatThousands(priceMin)}
                onChangeText={onChangePriceMin}
                placeholder="Min"
                inputMode="numeric"
                keyboardType="numeric"
                style={[styles.input, styles.inputHalf]}
              />
              <TextInput
                value={formatThousands(priceMax)}
                onChangeText={onChangePriceMax}
                placeholder="Max"
                inputMode="numeric"
                keyboardType="numeric"
                style={[styles.input, styles.inputHalf]}
              />
            </View>
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>Yıl</Text>
            <View style={styles.row}>
              <TextInput
                value={yearMin}
                onChangeText={setYearMin}
                placeholder="Min"
                inputMode="numeric"
                keyboardType="numeric"
                style={[styles.input, styles.inputHalf]}
              />
              <TextInput
                value={yearMax}
                onChangeText={setYearMax}
                placeholder="Max"
                inputMode="numeric"
                keyboardType="numeric"
                style={[styles.input, styles.inputHalf]}
              />
            </View>
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>Kilometre</Text>
            <View style={styles.row}>
              <TextInput
                value={formatThousands(kmMin)}
                onChangeText={onChangeKmMin}
                placeholder="Min"
                inputMode="numeric"
                keyboardType="numeric"
                style={[styles.input, styles.inputHalf]}
              />
              <TextInput
                value={formatThousands(kmMax)}
                onChangeText={onChangeKmMax}
                placeholder="Max"
                inputMode="numeric"
                keyboardType="numeric"
                style={[styles.input, styles.inputHalf]}
              />
            </View>
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Opsiyon Filtreleri</Text>
          <View style={styles.field}>
            <Text style={styles.label}>Yakıt Tipi</Text>
            <View style={styles.chipRow}>
              {FUEL_OPTIONS.map((opt) => {
                const selected = selectedFuels.includes(opt);
                return (
                  <Pressable
                    key={opt}
                    onPress={() => toggleSelection(selectedFuels, setSelectedFuels, opt)}
                    style={[styles.chip, selected && styles.chipSelected]}
                  >
                    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{opt}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>Vites</Text>
            <View style={styles.chipRow}>
              {TRANSMISSION_OPTIONS.map((opt) => {
                const selected = selectedTransmissions.includes(opt);
                return (
                  <Pressable
                    key={opt}
                    onPress={() =>
                      toggleSelection(selectedTransmissions, setSelectedTransmissions, opt)
                    }
                    style={[styles.chip, selected && styles.chipSelected]}
                  >
                    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{opt}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
          <View style={styles.field}>
            <Text style={styles.label}>Kasa Tipi</Text>
            <View style={styles.chipRow}>
              {BODY_OPTIONS.map((opt) => {
                const selected = selectedBodies.includes(opt);
                return (
                  <Pressable
                    key={opt}
                    onPress={() => toggleSelection(selectedBodies, setSelectedBodies, opt)}
                    style={[styles.chip, selected && styles.chipSelected]}
                  >
                    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{opt}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        </View>

        <Pressable onPress={handleSearch} style={styles.searchButton} disabled={creating}>
          <Text style={styles.searchButtonText}>{creating ? 'Kaydediliyor...' : 'Ara'}</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}
