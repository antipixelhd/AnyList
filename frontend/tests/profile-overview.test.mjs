import assert from 'node:assert/strict';
import test from 'node:test';
import { genreColor, overviewGenres, overviewDays } from '../src/lib/profile-overview.ts';

test('movie and series genre counts combine without changing genre colors', () => {
  const genres = overviewGenres([
    { genres: [{ genre: 'Drama', count: 12 }, { genre: 'Action', count: 4 }] },
    { genres: [{ genre: 'Drama', count: 6 }, { genre: 'Comedy', count: 4 }] },
    null,
  ]);
  assert.deepEqual(genres.map(({ genre, count }) => ({ genre, count })), [
    { genre: 'Drama', count: 18 }, { genre: 'Action', count: 4 }, { genre: 'Comedy', count: 4 },
  ]);
  assert.equal(genres[0].color, genreColor('Drama'));
  assert.equal(genreColor(' Action '), genreColor('action'));
  assert.equal(genreColor('Sci-Fi'), genreColor('Science Fiction'));
  assert.equal(genreColor('Experimental'), genreColor('experimental'));
  assert.deepEqual(overviewGenres([null, { genres: [] }]), []);
});

test('documented watch minutes convert to days with at most one decimal', () => {
  assert.equal(overviewDays(0), '0');
  assert.equal(overviewDays(1440), '1');
  assert.equal(overviewDays(176112), '122.3');
  assert.equal(overviewDays(120), '0.1');
});

test('equivalent genre names combine before sorting the complete bar by count', () => {
  const genres = overviewGenres([
    { genres: [{ genre: 'Drama', count: 4 }, { genre: 'Crime', count: 2 }, { genre: 'Science Fiction', count: 1 }, { genre: 'Adventure', count: 1 }] },
    { genres: [{ genre: 'Sci-Fi', count: 1 }, { genre: ' drama ', count: 1 }, { genre: 'Sci-Fi & Fantasy', count: 1 }] },
  ]);
  assert.deepEqual(genres.map(({ genre, count }) => [genre, count]), [
    ['Drama', 5], ['Crime', 2], ['Science Fiction', 2], ['Adventure', 1], ['Sci-Fi & Fantasy', 1],
  ]);
  assert.notEqual(genreColor('Science Fiction'), genreColor('Sci-Fi & Fantasy'));
  assert.notEqual(genreColor('Action'), genreColor('Action & Adventure'));
  assert.notEqual(genreColor('Horror'), genreColor('Supernatural'));
});
