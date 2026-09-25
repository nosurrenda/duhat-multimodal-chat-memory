package mediahash

import "testing"

func TestPortableCharacters(t *testing.T) {
	if err := AssertPortable("session/one_image:1.png"); err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"a&b", "a<b", "a>b", "café"} {
		if AssertPortable(value) == nil {
			t.Fatalf("%q must be rejected", value)
		}
	}
}
