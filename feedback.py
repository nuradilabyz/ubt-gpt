import streamlit as st
import streamlit.components.v1 as components

def feedback_page() -> None:
    st.title("Кері байланыс")
    st.write("Төмендегі форманы толтырыңыз:")

    components.html(
        """
        <iframe 
            src="https://docs.google.com/forms/d/e/1FAIpQLScOZ4NRZTZElIhpRrA_rf1hrRNPZ6HPvxdJL-Tnm8enQ-vfVw/viewform?embedded=true" 
            width="100%" 
            height="2200" 
            frameborder="0" 
            marginheight="0" 
            marginwidth="0">
        Загрузка…
        </iframe>
        """,
        height=2200,
    )

if __name__ == "__main__":
    feedback_page()
