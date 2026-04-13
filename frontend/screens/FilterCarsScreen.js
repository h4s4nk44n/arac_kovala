import React, { useEffect, useState } from 'react';
import { SafeAreaView, View, Text, FlatList, Pressable, Image, Linking } from 'react-native';

import { styles } from '../styles';
import { Api } from '../api';

export default function FilterCarsScreen({ route }) {
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
