import React, { useEffect, useState, useContext } from 'react';
import { SafeAreaView, View, Text, FlatList, Pressable, Alert } from 'react-native';

import { styles } from '../styles';
import { Api } from '../api';
import { FiltersContext } from '../context/FiltersContext';

export default function FiltersListScreen({ navigation }) {
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
