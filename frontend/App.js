import React, { useEffect, useMemo, useState, useContext, createContext, useCallback, useRef } from 'react';
import { SafeAreaView, ScrollView, View, Text, TextInput, Pressable, FlatList, Linking, Platform, Alert, Image } from 'react-native';
import { Picker } from '@react-native-picker/picker';
import { NavigationContainer, useNavigation } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';

import { styles, stylesPicker } from './styles';
import { Api } from './api';

// --- Notification Handler (Required by Expo) ---
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// --- Function to get the device's Push Token ---
async function registerForPushNotificationsAsync() {
  let token;
  if (Device.isDevice) {
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') {
      alert('Failed to get push token for push notification!');
      return;
    }
    try {
        token = await Notifications.getExpoPushTokenAsync({
            // *******************************************************************
            // IMPORTANT: REPLACE THIS WITH YOUR ACTUAL EXPO PROJECT ID
            // Find it in your `app.json` file under `expo.extra.eas.projectId`
            // *******************************************************************
            projectId: '7db31db4-91d2-4ca5-ab01-b02c50fa56a2', 
        });
        console.log('Expo Push Token:', token.data);
    } catch (e) {
        console.error("Failed to get push token", e);
        alert("Failed to get push token. Make sure you've set your Expo Project ID in App.js.");
    }
  } else {
    // alert('Must use physical device for Push Notifications');
    console.log('Skipping push notifications on simulator.');
  }

  if (Platform.OS === 'android') {
    Notifications.setNotificationChannelAsync('default', {
      name: 'default',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#FF231F7C',
    });
  }
  return token;
}


// Load local JSON datasets by car type
const otomobilData = require('./otomobil.json');
const araziSuvPickupData = require('./arazi_suv_pickup.json');

// --- Mappings for URL parameters ---
const FUEL_MAP = {
  'Benzinli': 'benzinli',
  'Dizel': 'dizel',
  'Benzin & LPG': 'benzin-lpg',
  'Hibrit': 'hibrit',
  'Elektrikli': 'elektrikli',
};

const TRANSMISSION_MAP = {
  'Manuel': 'manuel',
  'Otomatik': 'otomatik',
};

const BODY_MAP = {
  'Sedan': '250',
  'Hatchback': '62068', // 5 door
  'Station Wagon': '19',
  'Coupe': '48626',
  'Cabrio': '240',
};

// Static option lists shown in the UI
const FUEL_OPTIONS = Object.keys(FUEL_MAP);
const TRANSMISSION_OPTIONS = Object.keys(TRANSMISSION_MAP);
const BODY_OPTIONS = Object.keys(BODY_MAP);

// Global filters sync context
const FiltersContext = createContext({ filters: [], sync: async () => [], setFilters: () => {} });

function FiltersProvider({ children }) {
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

function FiltersEditorScreen() {
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
        // Sahibinden uses '?' for the first parameter and then nothing for sorting if it's the only one.
        // This logic handles adding the '?' correctly.
        return `${finalUrl}?${queryString}`;
    }
    return finalUrl; // Should not happen due to sorting=date_desc
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

function FeedScreen() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try {
      setLoading(true);
      const data = await Api.feed();
      setItems(data);
    } catch (e) {
      console.log('feed error', e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);

  const openPost = async (url) => {
    try {
      await Linking.openURL(url);
    } catch (e) {
      console.log('link error', e.message);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.container}>
        <Text style={styles.title}>Yeni İlanlar</Text>
        <FlatList
          data={items}
          refreshing={loading}
          onRefresh={load}
          keyExtractor={(item, index) => (item?.id ? String(item.id) : item?.url ? String(item.url) : String(index))}
          renderItem={({ item }) => (
            <Pressable onPress={() => openPost(item.url)} style={styles.resultItem}>
              <View style={styles.resultItemRow}>
                {item.image_url ? (
                  <Image source={{ uri: item.image_url }} style={styles.thumb} resizeMode="cover" />
                ) : (
                  <View style={styles.thumb} />
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.sectionTitle}>{item.brand} {item.serie} {item.model}</Text>
                  <Text style={styles.resultText}>
                    {item.price} · {item.year} · {(typeof item.km === 'number' ? item.km.toLocaleString('tr-TR') : String(item.km || ''))} km
                  </Text>
                </View>
              </View>
            </Pressable>
          )}
          ItemSeparatorComponent={() => <View style={styles.separator} />}
          ListEmptyComponent={<Text style={styles.placeholderText}>Henüz ilan yok.</Text>}
        />
      </View>
    </SafeAreaView>
  );
}

function FiltersListScreen({ navigation }) {
  const { filters, sync } = useContext(FiltersContext);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try {
      setLoading(true);
      await sync();
    } catch (e) {
      console.log('filters error', e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const unsub = navigation.addListener('focus', load);
    load();
    return unsub;
  }, [navigation]);

  const onDelete = async (id) => {
    try {
      await Api.deleteFilter(id);
      await sync();
    } catch (e) {
      console.log('delete filter error', e.message);
      Alert.alert('Hata', e.message || 'Silinemedi');
    }
  };

  const confirmDelete = (id) => {
    Alert.alert('Sil', 'Bu filtreyi silmek istediğinize emin misiniz?', [
      { text: 'İptal', style: 'cancel' },
      { text: 'Sil', style: 'destructive', onPress: () => onDelete(id) },
    ]);
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={[styles.container, { flex: 1 }]}>
        <Text style={styles.title}>Filtreler</Text>
        <FlatList
          data={filters}
          refreshing={loading}
          onRefresh={load}
          keyExtractor={(item) => String(item.id)}
          renderItem={({ item }) => (
            <View style={[styles.resultItem, { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }]}>
              <Pressable
                style={{ flex: 1 }}
                onPress={() => navigation.navigate('FilterCars', { id: item.id, name: item.name })}
              >
                <Text style={styles.sectionTitle}>{item.name}</Text>
                <Text style={styles.resultText} numberOfLines={1}>{item.url}</Text>
              </Pressable>
              <Pressable onPress={() => confirmDelete(item.id)} style={styles.deleteButton}>
                <Text style={styles.deleteButtonText}>Sil</Text>
              </Pressable>
            </View>
          )}
          ItemSeparatorComponent={() => <View style={styles.separator} />}
          ListEmptyComponent={<Text style={styles.placeholderText}>Hiç filtre yok.</Text>}
        />
      </View>
    </SafeAreaView>
  );
}

function FilterCarsScreen({ route }) {
  const { id, name } = route.params;
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try {
      setLoading(true);
      const data = await Api.filterCars(id);
      setItems(data);
    } catch (e) {
      console.log('filter cars error', e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [id]);

  const openPost = async (url) => {
    try {
      await Linking.openURL(url);
    } catch (e) {
      console.log('link error', e.message);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.container}>
        <Text style={styles.title}>{name}</Text>
        <FlatList
          data={items}
          refreshing={loading}
          onRefresh={load}
          keyExtractor={(item, index) => (item?.id ? String(item.id) : item?.url ? String(item.url) : String(index))}
          renderItem={({ item }) => (
            <Pressable onPress={() => openPost(item.url)} style={styles.resultItem}>
              <View style={styles.resultItemRow}>
                {item.image_url ? (
                  <Image source={{ uri: item.image_url }} style={styles.thumb} resizeMode="cover" />
                ) : (
                  <View style={styles.thumb} />
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.sectionTitle}>{item.brand} {item.serie} {item.model}</Text>
                  <Text style={styles.resultText}>
                    {item.price} · {item.year} · {(typeof item.km === 'number' ? item.km.toLocaleString('tr-TR') : String(item.km || ''))} km
                  </Text>
                </View>
              </View>
            </Pressable>
          )}
          ItemSeparatorComponent={() => <View style={styles.separator} />}
          ListEmptyComponent={<Text style={styles.placeholderText}>Bu filtre için ilan yok.</Text>}
        />
      </View>
    </SafeAreaView>
  );
}

const Tab = createBottomTabNavigator();
const Stack = createNativeStackNavigator();

function FiltersStack() {
  return (
    <Stack.Navigator>
      <Stack.Screen name="FiltersList" component={FiltersListScreen} options={{ title: 'Filtreler' }} />
      <Stack.Screen name="FilterCars" component={FilterCarsScreen} options={({ route }) => ({ title: route.params?.name || 'İlanlar' })} />
    </Stack.Navigator>
  );
}

export default function App() {
    const notificationListener = useRef();
    const responseListener = useRef();

    useEffect(() => {
        // 1. Register for push notifications and send token to backend
        registerForPushNotificationsAsync().then(token => {
            if (token) {
                Api.registerPushToken({ token: token.data })
                .then(() => console.log('Successfully registered push token with backend.'))
                .catch(e => console.error('Failed to register push token:', e));
            }
        });

        // 2. This listener is fired whenever a notification is received while the app is foregrounded
        notificationListener.current = Notifications.addNotificationReceivedListener(notification => {
            console.log('Notification received:', notification);
        });

        // 3. This listener is fired whenever a user taps on or interacts with a notification
        responseListener.current = Notifications.addNotificationResponseReceivedListener(response => {
            console.log('Notification response:', response);
            const { url } = response.notification.request.content.data;
            if (url) {
                Linking.openURL(url);
            }
        });

        return () => {
            Notifications.removeNotificationSubscription(notificationListener.current);
            Notifications.removeNotificationSubscription(responseListener.current);
        };
    }, []);

    return (
        <NavigationContainer>
        <FiltersProvider>
            <Tab.Navigator>
            <Tab.Screen name="Filtre ekle" component={FiltersEditorScreen} />
            <Tab.Screen name="Yeni İlanlar" component={FeedScreen} />
            <Tab.Screen name="Filtreler" component={FiltersStack} options={{ headerShown: false }} />
            </Tab.Navigator>
        </FiltersProvider>
        </NavigationContainer>
    );
}